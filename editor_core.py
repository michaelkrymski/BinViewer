"""Shared editing core for web and CLI BIN editing flows."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
import base64
import json
import math
import os
import re
import uuid
import zipfile

from bin_reader import (
    _apply_math,
    axis_cell_address,
    extract_table,
    get_axis_layout,
    pack_raw_value,
    raw_numeric_range,
)
from table_ranker import build_table_metadata, load_description_overlay, load_tuning_history
from xdf_parser import parse_xdf

DEFAULT_EXPORT_LOG_DIR = Path.home() / 'Documents' / 'N55Maps' / 'viewer_export_logs'

SUPPORTED_OPERATIONS = [
    'set_cells',
    'add_constant',
    'multiply',
    'interpolate_x',
    'interpolate_y',
    'interpolate_xy',
]

OPERATION_ALIASES = {
    'set': 'set_cells',
    'set_cells': 'set_cells',
    'add': 'add_constant',
    'add_constant': 'add_constant',
    'multiply': 'multiply',
    'mul': 'multiply',
    'interpolate-x': 'interpolate_x',
    'interpolate_x': 'interpolate_x',
    'interpolate-y': 'interpolate_y',
    'interpolate_y': 'interpolate_y',
    'interpolate-xy': 'interpolate_xy',
    'interpolate_xy': 'interpolate_xy',
}


@dataclass
class TableBinding:
    """Stable lookup record for one parsed table definition."""

    table_id: str
    index: int
    table_def: dict
    metadata: dict


class SessionRegistry:
    """In-memory session registry for the Flask app."""

    def __init__(self) -> None:
        self._sessions: dict[str, EditSession] = {}

    def create(self, **kwargs) -> 'EditSession':
        session = EditSession(**kwargs)
        self._sessions[session.id] = session
        return session

    def store(self, session: 'EditSession') -> 'EditSession':
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> 'EditSession':
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise KeyError(f'Unknown session: {session_id}') from exc

    def discard(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


class EditSession:
    """Stateful editing session backed by an XDF and working BIN bytes."""

    def __init__(
        self,
        *,
        xdf_bytes: bytes,
        bin_bytes: bytes,
        xdf_name: str = 'input.xdf',
        bin_name: str = 'input.bin',
        bin2_bytes: bytes | None = None,
        bin2_name: str | None = None,
        session_id: str | None = None,
        created_at: str | None = None,
    ) -> None:
        load_tuning_history()
        load_description_overlay()

        self.id = session_id or uuid.uuid4().hex
        self.created_at = created_at or datetime.now().isoformat(timespec='seconds')
        self.source_xdf_bytes = bytes(xdf_bytes)
        self.source_xdf_name = xdf_name
        self.source_bin = bytes(bin_bytes)
        self.source_bin_name = bin_name
        self.reference_bin = bytes(bin2_bytes) if bin2_bytes is not None else None
        self.reference_bin_name = bin2_name
        self.working_bin = bytearray(bin_bytes)
        self.pending_edits: list[dict] = []
        self.dirty_table_ids: set[str] = set()
        self._history: list[dict] = []

        self.table_defs, self.categories_map = parse_xdf(self.source_xdf_bytes)
        self.bindings: list[TableBinding] = []
        self._binding_by_id: dict[str, TableBinding] = {}
        self._source_tables: dict[str, dict] = {}
        self._current_tables: dict[str, dict] = {}

        for index, table_def in enumerate(self.table_defs):
            table_id = make_table_id(table_def, index)
            metadata = build_table_metadata(table_def, self.categories_map)
            binding = TableBinding(table_id=table_id, index=index, table_def=table_def, metadata=metadata)
            self.bindings.append(binding)
            self._binding_by_id[table_id] = binding

        self._rebuild_all_tables()

    @classmethod
    def from_payload(cls, payload: dict) -> 'EditSession':
        """Restore a session from a CLI session file payload."""
        session = cls(
            xdf_bytes=base64.b64decode(payload['source_xdf_b64']),
            bin_bytes=base64.b64decode(payload['source_bin_b64']),
            xdf_name=payload.get('source_xdf_name', 'input.xdf'),
            bin_name=payload.get('source_bin_name', 'input.bin'),
            bin2_bytes=base64.b64decode(payload['reference_bin_b64']) if payload.get('reference_bin_b64') else None,
            bin2_name=payload.get('reference_bin_name'),
            session_id=payload.get('session_id'),
            created_at=payload.get('created_at'),
        )
        saved_edits = payload.get('pending_edits', [])
        if saved_edits:
            for edit in saved_edits:
                operation = edit.get('operation', '')
                if operation in ('axis_x_set', 'axis_y_set'):
                    axis_name = 'x' if operation == 'axis_x_set' else 'y'
                    session.apply_axis_edit(
                        edit['table_id'],
                        axis_name,
                        (edit.get('parameters') or {}).get('values') or [],
                    )
                else:
                    session.apply_operation(
                        edit['table_id'],
                        operation,
                        edit.get('selection'),
                        edit.get('parameters') or {},
                    )
            session.pending_edits = saved_edits

        working_bin_b64 = payload.get('working_bin_b64')
        if working_bin_b64:
            session.working_bin = bytearray(base64.b64decode(working_bin_b64))
            session._rebuild_current_tables()
        session.dirty_table_ids = set(payload.get('dirty_table_ids', session.dirty_table_ids))
        session._recompute_dirty_tables()
        return session

    def to_payload(self) -> dict:
        """Serialize the session to a JSON-safe payload for CLI reuse."""
        return {
            'version': 1,
            'session_id': self.id,
            'created_at': self.created_at,
            'source_xdf_name': self.source_xdf_name,
            'source_bin_name': self.source_bin_name,
            'reference_bin_name': self.reference_bin_name,
            'source_xdf_b64': base64.b64encode(self.source_xdf_bytes).decode('ascii'),
            'source_bin_b64': base64.b64encode(self.source_bin).decode('ascii'),
            'reference_bin_b64': (
                base64.b64encode(self.reference_bin).decode('ascii') if self.reference_bin is not None else None
            ),
            'working_bin_b64': base64.b64encode(bytes(self.working_bin)).decode('ascii'),
            'pending_edits': self.pending_edits,
            'dirty_table_ids': sorted(self.dirty_table_ids),
        }

    def list_tables(self) -> list[dict]:
        """Return current table entries in their parsed order."""
        return [deepcopy(self._current_tables[binding.table_id]) for binding in self.bindings]

    def get_table(self, table_id: str) -> dict:
        """Return one current table entry."""
        return deepcopy(self._current_tables[self._require_binding(table_id).table_id])

    def get_summary(self) -> dict:
        """Return session-level state used by the UI and CLI."""
        return {
            'session_id': self.id,
            'created_at': self.created_at,
            'source_xdf_name': self.source_xdf_name,
            'source_bin_name': self.source_bin_name,
            'reference_bin_name': self.reference_bin_name,
            'has_reference_bin': self.reference_bin is not None,
            'pending_change_count': len(self.pending_edits),
            'dirty_table_count': len(self.dirty_table_ids),
            'dirty_table_ids': sorted(self.dirty_table_ids),
            'export_ready': bool(self.pending_edits),
        }

    def preview_operation(self, table_id: str, operation: str, selection: dict | None, parameters: dict | None = None) -> dict:
        """Preview an edit operation without mutating the working BIN."""
        self._require_binding(table_id)
        entry = self._current_tables[table_id]
        if not entry.get('editable'):
            raise ValueError(f'Table {table_id} is not editable')
        if entry.get('error'):
            raise ValueError(f"Table {table_id} cannot be edited: {entry['error']}")

        parameters = parameters or {}
        normalized_operation = normalize_operation_name(operation)
        normalized_selection = normalize_selection(selection or {}, entry['rows'], entry['cols'])
        decimal_places = entry.get('z_decimal_places', -1)
        before_grid = deepcopy(entry['grid'])
        operation_grid = quantize_grid_region_for_display(before_grid, normalized_selection, decimal_places)
        after_grid = apply_operation_to_grid(operation_grid, normalized_selection, normalized_operation, parameters)
        after_grid = quantize_grid_region_for_display(after_grid, normalized_selection, decimal_places)
        changes = summarize_changes(before_grid, after_grid)

        return {
            'table_id': table_id,
            'table_title': entry['title'],
            'selection': normalized_selection,
            'operation': normalized_operation,
            'parameters': sanitize_parameters(parameters),
            'summary': build_preview_summary(changes),
            'changes': changes[:160],
            'changes_truncated': len(changes) > 160,
            'after_grid': after_grid,
            'selection_bounds': entry['selection_bounds'],
        }

    def apply_operation(self, table_id: str, operation: str, selection: dict | None, parameters: dict | None = None) -> dict:
        """Apply an edit operation and write the result into the working BIN."""
        preview = self.preview_operation(table_id, operation, selection, parameters)
        binding = self._require_binding(table_id)

        self._history.append({
            'working_bin': bytes(self.working_bin),
            'pending_edits': deepcopy(self.pending_edits),
            'dirty_table_ids': set(self.dirty_table_ids),
        })

        write_grid_to_bin(self.working_bin, binding.table_def, preview['after_grid'])
        self._refresh_table(table_id)

        edit_record = {
            'timestamp': datetime.now().isoformat(timespec='seconds'),
            'table_id': table_id,
            'table_title': self._current_tables[table_id]['title'],
            'operation': preview['operation'],
            'selection': preview['selection'],
            'parameters': preview['parameters'],
            'summary': preview['summary'],
        }
        self.pending_edits.append(edit_record)
        return {
            'preview': drop_preview_grid(preview),
            'table': deepcopy(self._current_tables[table_id]),
            'session': self.get_summary(),
            'pending_edits': deepcopy(self.pending_edits),
        }

    def paste_values(self, table_id: str, start_row: int, start_col: int, values: list[list[float]]) -> dict:
        """Paste a rectangular value block into a table, clipping at table bounds."""
        binding = self._require_binding(table_id)
        entry = self._current_tables[table_id]
        if not entry.get('editable'):
            raise ValueError(f'Table {table_id} is not editable')
        if entry.get('error'):
            raise ValueError(f"Table {table_id} cannot be edited: {entry['error']}")

        start_row = int(start_row)
        start_col = int(start_col)
        if start_row < 0 or start_row >= entry['rows']:
            raise ValueError(f'Paste row {start_row} outside table bounds 0:{entry["rows"] - 1}')
        if start_col < 0 or start_col >= entry['cols']:
            raise ValueError(f'Paste column {start_col} outside table bounds 0:{entry["cols"] - 1}')

        clean_values = normalize_paste_values(values)
        if not clean_values:
            raise ValueError('Paste values must include at least one numeric cell')

        before_grid = deepcopy(entry['grid'])
        after_grid = deepcopy(before_grid)
        pasted_count = 0
        for row_offset, row_values in enumerate(clean_values):
            target_row = start_row + row_offset
            if target_row >= entry['rows']:
                break
            for col_offset, value in enumerate(row_values):
                target_col = start_col + col_offset
                if target_col >= entry['cols']:
                    break
                after_grid[target_row][target_col] = value
                pasted_count += 1

        if pasted_count == 0:
            raise ValueError('Paste target is outside the editable table area')

        changes = summarize_changes(before_grid, after_grid)
        self._history.append({
            'working_bin': bytes(self.working_bin),
            'pending_edits': deepcopy(self.pending_edits),
            'dirty_table_ids': set(self.dirty_table_ids),
        })

        write_grid_to_bin(self.working_bin, binding.table_def, after_grid)
        self._refresh_table(table_id)

        row_count = len(clean_values)
        col_count = max(len(row) for row in clean_values)
        selection = {
            'row_start': start_row,
            'row_end': min(entry['rows'] - 1, start_row + row_count - 1),
            'col_start': start_col,
            'col_end': min(entry['cols'] - 1, start_col + col_count - 1),
            'is_rectangular': True,
        }
        preview = {
            'table_id': table_id,
            'table_title': entry['title'],
            'selection': selection,
            'operation': 'paste_values',
            'parameters': {'row_count': row_count, 'col_count': col_count},
            'summary': build_preview_summary(changes),
            'changes': changes[:160],
            'changes_truncated': len(changes) > 160,
            'after_grid': after_grid,
            'selection_bounds': entry['selection_bounds'],
        }
        edit_record = {
            'timestamp': datetime.now().isoformat(timespec='seconds'),
            'table_id': table_id,
            'table_title': self._current_tables[table_id]['title'],
            'operation': 'paste_values',
            'selection': selection,
            'parameters': preview['parameters'],
            'summary': preview['summary'],
        }
        self.pending_edits.append(edit_record)
        self._recompute_dirty_tables()
        return {
            'preview': drop_preview_grid(preview),
            'table': deepcopy(self._current_tables[table_id]),
            'session': self.get_summary(),
            'pending_edits': deepcopy(self.pending_edits),
        }

    def apply_axis_edit(self, table_id: str, axis_name: str, values: list[float]) -> dict:
        """Write X or Y axis breakpoint values into the working BIN."""
        binding = self._require_binding(table_id)
        table = self._current_tables[table_id]
        normalized_axis = normalize_axis_name(axis_name)
        current_values = list(table['x_labels'] if normalized_axis == 'x' else table['y_labels'])
        if not current_values:
            raise ValueError(f'Table {table_id} does not expose an editable {normalized_axis.upper()} axis')
        if len(values) != len(current_values):
            raise ValueError(
                f'{normalized_axis.upper()} axis expects {len(current_values)} values, got {len(values)}'
            )

        self._history.append({
            'working_bin': bytes(self.working_bin),
            'pending_edits': deepcopy(self.pending_edits),
            'dirty_table_ids': set(self.dirty_table_ids),
        })

        changes = [
            {
                'index': index,
                'before': current_values[index],
                'after': values[index],
                'delta': values[index] - current_values[index],
            }
            for index in range(len(values))
            if not values_close(current_values[index], values[index])
        ]
        changed_indices = [change['index'] for change in changes]

        write_axis_values_to_bin(
            self.working_bin,
            binding.table_def,
            normalized_axis,
            values,
            changed_indices=changed_indices,
        )
        self._refresh_table(table_id)

        edit_record = {
            'timestamp': datetime.now().isoformat(timespec='seconds'),
            'table_id': table_id,
            'table_title': self._current_tables[table_id]['title'],
            'operation': f'axis_{normalized_axis}_set',
            'selection': {'axis': normalized_axis, 'count': len(values)},
            'parameters': {'values': values},
            'summary': {
                'changed_cell_count': len(changes),
                'min_delta': min((change['delta'] for change in changes), default=0.0),
                'max_delta': max((change['delta'] for change in changes), default=0.0),
            },
        }
        self.pending_edits.append(edit_record)
        return {
            'table': deepcopy(self._current_tables[table_id]),
            'changes': changes[:160],
            'changes_truncated': len(changes) > 160,
            'session': self.get_summary(),
            'pending_edits': deepcopy(self.pending_edits),
        }

    def preview_import(self, table_id: str, import_text: str, *, mode: str = 'values_only') -> dict:
        """Parse an imported AI table block and preview the resulting table/axis changes."""
        self._require_binding(table_id)
        entry = self._current_tables[table_id]
        if not entry.get('editable'):
            raise ValueError(f'Table {table_id} is not editable')
        if entry.get('error'):
            raise ValueError(f"Table {table_id} cannot be edited: {entry['error']}")

        parsed = parse_table_import_text(import_text, entry, mode=mode)
        after_grid = deepcopy(parsed['grid']) if parsed['grid'] is not None else deepcopy(entry['grid'])
        changes = summarize_changes(entry['grid'], after_grid)
        x_changes = summarize_axis_changes(entry.get('x_labels') or [], parsed.get('x_labels'))
        y_changes = summarize_axis_changes(entry.get('y_labels') or [], parsed.get('y_labels'))

        return {
            'table_id': table_id,
            'table_title': entry['title'],
            'mode': parsed['mode'],
            'parsed_title': parsed.get('title'),
            'summary': build_preview_summary(changes),
            'changes': changes[:160],
            'changes_truncated': len(changes) > 160,
            'after_grid': after_grid,
            'import_grid': deepcopy(parsed['grid']) if parsed['grid'] is not None else None,
            'import_x_labels': deepcopy(parsed.get('x_labels')),
            'import_y_labels': deepcopy(parsed.get('y_labels')),
            'axis_summary': {
                'x_changed_count': len(x_changes),
                'y_changed_count': len(y_changes),
                'x_changes': x_changes[:80],
                'y_changes': y_changes[:80],
            },
            'validation': parsed['validation'],
        }

    def apply_import(self, table_id: str, import_text: str, *, mode: str = 'values_only') -> dict:
        """Apply an imported AI table block into the draft session."""
        preview = self.preview_import(table_id, import_text, mode=mode)
        binding = self._require_binding(table_id)

        self._history.append({
            'working_bin': bytes(self.working_bin),
            'pending_edits': deepcopy(self.pending_edits),
            'dirty_table_ids': set(self.dirty_table_ids),
        })

        if preview['import_grid'] is not None:
            write_grid_to_bin(self.working_bin, binding.table_def, preview['import_grid'])

        if preview['import_x_labels'] is not None:
            write_axis_values_to_bin(
                self.working_bin,
                binding.table_def,
                'x',
                preview['import_x_labels'],
                changed_indices=[change['index'] for change in preview['axis_summary']['x_changes']],
            )

        if preview['import_y_labels'] is not None:
            write_axis_values_to_bin(
                self.working_bin,
                binding.table_def,
                'y',
                preview['import_y_labels'],
                changed_indices=[change['index'] for change in preview['axis_summary']['y_changes']],
            )

        self._refresh_table(table_id)
        refreshed_table = self._current_tables[table_id]
        edit_record = {
            'timestamp': datetime.now().isoformat(timespec='seconds'),
            'table_id': table_id,
            'table_title': refreshed_table['title'],
            'operation': 'import_table',
            'selection': {'rows': refreshed_table['rows'], 'cols': refreshed_table['cols']},
            'parameters': {
                'mode': preview['mode'],
                'parsed_title': preview.get('parsed_title'),
                'x_axis_changed': preview['axis_summary']['x_changed_count'],
                'y_axis_changed': preview['axis_summary']['y_changed_count'],
            },
            'summary': preview['summary'],
        }
        self.pending_edits.append(edit_record)
        return {
            'preview': drop_preview_grid(preview),
            'table': deepcopy(refreshed_table),
            'session': self.get_summary(),
            'pending_edits': deepcopy(self.pending_edits),
        }

    def transfer_reference_table(self, table_id: str, *, include_axes: bool = False) -> dict:
        """Copy the reference BIN's values for one table into the working draft."""
        if self.reference_bin is None:
            raise ValueError('No compare/reference BIN is loaded for this session.')

        binding = self._require_binding(table_id)
        current_entry = self._current_tables[table_id]
        if current_entry.get('error'):
            raise ValueError(f"Table {table_id} cannot be transferred: {current_entry['error']}")
        if not current_entry.get('editable'):
            raise ValueError(f'Table {table_id} is not editable and cannot be transferred.')

        reference_data = extract_table(self.reference_bin, binding.table_def)
        value_changes = summarize_changes(current_entry['grid'], reference_data['grid'])
        x_changes = summarize_axis_changes(current_entry.get('x_labels') or [], reference_data.get('x_labels'))
        y_changes = summarize_axis_changes(current_entry.get('y_labels') or [], reference_data.get('y_labels'))

        self._history.append({
            'working_bin': bytes(self.working_bin),
            'pending_edits': deepcopy(self.pending_edits),
            'dirty_table_ids': set(self.dirty_table_ids),
        })

        write_grid_to_bin(self.working_bin, binding.table_def, reference_data['grid'])

        if include_axes and current_entry.get('x_axis_editable') and x_changes:
            write_axis_values_to_bin(
                self.working_bin,
                binding.table_def,
                'x',
                reference_data['x_labels'],
                changed_indices=[change['index'] for change in x_changes],
            )
        if include_axes and current_entry.get('y_axis_editable') and y_changes:
            write_axis_values_to_bin(
                self.working_bin,
                binding.table_def,
                'y',
                reference_data['y_labels'],
                changed_indices=[change['index'] for change in y_changes],
            )

        self._refresh_table(table_id)
        refreshed_table = self._current_tables[table_id]
        self.pending_edits.append({
            'timestamp': datetime.now().isoformat(timespec='seconds'),
            'table_id': table_id,
            'table_title': refreshed_table['title'],
            'operation': 'transfer_reference',
            'selection': {'rows': refreshed_table['rows'], 'cols': refreshed_table['cols']},
            'parameters': {
                'include_axes': include_axes,
                'reference_bin_name': self.reference_bin_name,
                'x_axis_changed': len(x_changes) if include_axes else 0,
                'y_axis_changed': len(y_changes) if include_axes else 0,
            },
            'summary': build_preview_summary(value_changes),
        })
        return {
            'table': deepcopy(refreshed_table),
            'session': self.get_summary(),
            'pending_edits': deepcopy(self.pending_edits),
            'summary': {
                'changed_cell_count': len(value_changes),
                'x_axis_changed_count': len(x_changes) if include_axes else 0,
                'y_axis_changed_count': len(y_changes) if include_axes else 0,
            },
        }

    def undo_last(self) -> dict:
        """Undo the latest applied edit."""
        if not self._history:
            raise ValueError('No applied edits to undo')

        snapshot = self._history.pop()
        self.working_bin = bytearray(snapshot['working_bin'])
        self.pending_edits = snapshot['pending_edits']
        self.dirty_table_ids = set(snapshot['dirty_table_ids'])
        self._rebuild_current_tables()
        return {
            'session': self.get_summary(),
            'pending_edits': deepcopy(self.pending_edits),
        }

    def reset_draft(self) -> dict:
        """Reset the draft back to the original BIN."""
        self._history.clear()
        self.working_bin = bytearray(self.source_bin)
        self.pending_edits = []
        self.dirty_table_ids = set()
        self._rebuild_current_tables()
        return {
            'session': self.get_summary(),
            'pending_edits': [],
        }

    def export_bundle(self, *, stem: str | None = None) -> tuple[str, bytes]:
        """Return a ZIP bundle containing the edited BIN and a JSON change log."""
        base_stem = sanitize_stem(stem or Path(self.source_bin_name).stem or 'edited-bin')
        bin_name = f'{base_stem}-edited.bin'
        log_name = f'{base_stem}-edited.changes.json'

        buffer = BytesIO()
        with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(bin_name, bytes(self.working_bin))
            archive.writestr(
                log_name,
                json.dumps(
                    {
                        'session': self.get_summary(),
                        'pending_edits': self.pending_edits,
                    },
                    indent=2,
                ).encode('utf-8'),
            )
        return (f'{base_stem}-edited.zip', buffer.getvalue())

    def export_bin_only(self, *, stem: str | None = None, log_dir: str | Path | None = None) -> dict:
        """Return raw BIN bytes and persist a central JSON change log for the export."""
        base_stem = sanitize_stem(stem or Path(self.source_bin_name).stem or 'edited-bin')
        bin_name = f'{base_stem}-edited.bin'
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        target_dir = Path(log_dir) if log_dir else DEFAULT_EXPORT_LOG_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        log_path = target_dir / f'{base_stem}-{timestamp}.changes.json'
        log_payload = {
            'session': self.get_summary(),
            'pending_edits': self.pending_edits,
            'source_bin_name': self.source_bin_name,
            'reference_bin_name': self.reference_bin_name,
            'exported_bin_name': bin_name,
            'exported_at': datetime.now().isoformat(timespec='seconds'),
        }
        log_path.write_text(json.dumps(log_payload, indent=2), encoding='utf-8')
        return {
            'filename': bin_name,
            'bin_bytes': bytes(self.working_bin),
            'log_path': str(log_path),
            'session': self.get_summary(),
        }

    def export_bin_and_log(self, output_path: str | Path, log_dir: str | Path | None = None) -> dict:
        """Write the draft BIN and a companion JSON change log to disk."""
        output_path = Path(output_path)
        output_path.write_bytes(bytes(self.working_bin))
        if log_dir:
            log_path = Path(log_dir) / f'{output_path.name}.changes.json'
            log_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            log_path = output_path.with_suffix(output_path.suffix + '.changes.json')
        export_entry = {
            'session': self.get_summary(),
            'pending_edits': self.pending_edits,
            'exported_at': datetime.now().isoformat(timespec='seconds'),
        }
        if log_path.exists():
            try:
                existing = json.loads(log_path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                existing = {}
            if isinstance(existing, dict) and isinstance(existing.get('exports'), list):
                log_payload = existing
                log_payload['exports'].append(export_entry)
                log_payload['session'] = self.get_summary()
                log_payload['pending_edits'] = self.pending_edits
            else:
                log_payload = {
                    'session': self.get_summary(),
                    'pending_edits': self.pending_edits,
                    'exports': [export_entry],
                }
        else:
            log_payload = {
                'session': self.get_summary(),
                'pending_edits': self.pending_edits,
                'exports': [export_entry],
            }
        log_path.write_text(json.dumps(log_payload, indent=2), encoding='utf-8')
        return {
            'bin_path': str(output_path),
            'log_path': str(log_path),
            'session': self.get_summary(),
        }

    def diff_summary(self) -> dict:
        """Return a compact summary of draft changes."""
        return {
            'session': self.get_summary(),
            'pending_edits': deepcopy(self.pending_edits),
            'changed_tables': [deepcopy(self._current_tables[table_id]) for table_id in self.dirty_table_ids],
        }

    def _require_binding(self, table_id: str) -> TableBinding:
        try:
            return self._binding_by_id[table_id]
        except KeyError as exc:
            raise KeyError(f'Unknown table_id: {table_id}') from exc

    def _rebuild_all_tables(self) -> None:
        for binding in self.bindings:
            entry = self._serialize_binding(binding, bytes(self.source_bin))
            self._source_tables[binding.table_id] = entry
        self._rebuild_current_tables()

    def _rebuild_current_tables(self) -> None:
        for binding in self.bindings:
            self._current_tables[binding.table_id] = self._serialize_binding(binding, bytes(self.working_bin))
        self._recompute_dirty_tables()

    def _refresh_table(self, table_id: str) -> None:
        binding = self._require_binding(table_id)
        self._current_tables[table_id] = self._serialize_binding(binding, bytes(self.working_bin))
        source_entry = self._source_tables[table_id]
        current_entry = self._current_tables[table_id]
        if table_entries_equal(source_entry, current_entry):
            self.dirty_table_ids.discard(table_id)
        else:
            self.dirty_table_ids.add(table_id)
        self._current_tables[table_id]['is_dirty'] = table_id in self.dirty_table_ids

    def _recompute_dirty_tables(self) -> None:
        dirty: set[str] = set()
        for binding in self.bindings:
            table_id = binding.table_id
            if not table_entries_equal(self._source_tables[table_id], self._current_tables[table_id]):
                dirty.add(table_id)
        self.dirty_table_ids = dirty
        for table_id in self._current_tables:
            self._current_tables[table_id]['is_dirty'] = table_id in self.dirty_table_ids

    def _serialize_binding(self, binding: TableBinding, bin_data: bytes) -> dict:
        table_def = binding.table_def
        metadata = binding.metadata

        try:
            data = extract_table(bin_data, table_def)
            reference_data = extract_table(self.reference_bin, table_def) if self.reference_bin is not None else None
            editable = bool((table_def.get('z_axis') or {}).get('embedded'))
            entry = {
                'table_id': binding.table_id,
                'title': table_def['title'],
                'description': table_def.get('description', ''),
                'type': table_def['type'],
                'units': data['units'],
                'grid': data['grid'],
                'x_labels': data['x_labels'],
                'y_labels': data['y_labels'],
                'rows': data['rows'],
                'cols': data['cols'],
                'has_diff': reference_data is not None,
                'grid2': reference_data['grid'] if reference_data else None,
                'x_labels2': reference_data['x_labels'] if reference_data else None,
                'y_labels2': reference_data['y_labels'] if reference_data else None,
                'error': None,
                'category_indices': table_def.get('category_indices', []),
                'is_autogen': table_def.get('is_autogen', False),
                'editable': editable,
                'editable_operations': SUPPORTED_OPERATIONS[:] if editable else [],
                'z_decimal_places': (table_def.get('z_axis') or {}).get('decimal_places', -1),
                'x_axis_editable': bool((table_def.get('x_axis') or {}).get('embedded')),
                'y_axis_editable': bool((table_def.get('y_axis') or {}).get('embedded')),
                'selection_bounds': {
                    'row_min': 0,
                    'row_max': max(data['rows'] - 1, 0),
                    'col_min': 0,
                    'col_max': max(data['cols'] - 1, 0),
                },
                'is_dirty': binding.table_id in self.dirty_table_ids,
                **metadata,
            }
        except Exception as exc:
            entry = {
                'table_id': binding.table_id,
                'title': table_def.get('title', 'Unknown'),
                'description': table_def.get('description', ''),
                'type': table_def.get('type', 'unknown'),
                'units': '',
                'grid': [],
                'x_labels': [],
                'y_labels': [],
                'rows': 0,
                'cols': 0,
                'has_diff': False,
                'grid2': None,
                'x_labels2': None,
                'y_labels2': None,
                'error': str(exc),
                'category_indices': table_def.get('category_indices', []),
                'is_autogen': table_def.get('is_autogen', False),
                'editable': False,
                'editable_operations': [],
                'z_decimal_places': (table_def.get('z_axis') or {}).get('decimal_places', -1),
                'x_axis_editable': False,
                'y_axis_editable': False,
                'selection_bounds': {
                    'row_min': 0,
                    'row_max': 0,
                    'col_min': 0,
                    'col_max': 0,
                },
                'is_dirty': False,
                **metadata,
            }
        return entry


def make_table_id(table_def: dict, index: int) -> str:
    """Create a stable table identifier."""
    base = table_def.get('unique_id') or slugify(table_def.get('title', 'table'))
    return f"{table_def.get('type', 'table')}-{index:04d}-{slugify(base)[:48]}"


def normalize_operation_name(value: str) -> str:
    """Normalize web/CLI operation aliases into canonical names."""
    key = str(value or '').strip().lower()
    try:
        return OPERATION_ALIASES[key]
    except KeyError as exc:
        raise ValueError(f'Unsupported operation: {value}') from exc


def normalize_selection(selection: dict, rows: int, cols: int) -> dict:
    """Normalize and validate a rectangular or explicit-cell selection."""
    if rows <= 0 or cols <= 0:
        raise ValueError('Selection is only available for non-empty tables')

    raw_cells = selection.get('cells')
    if isinstance(raw_cells, list) and raw_cells:
        cells: list[dict] = []
        seen: set[tuple[int, int]] = set()
        for item in raw_cells:
            if not isinstance(item, dict):
                continue
            row = int(item.get('row', 0))
            col = int(item.get('col', 0))
            if row < 0 or row >= rows:
                raise ValueError(f'Row selection {row} outside table bounds 0:{rows - 1}')
            if col < 0 or col >= cols:
                raise ValueError(f'Column selection {col} outside table bounds 0:{cols - 1}')
            key = (row, col)
            if key in seen:
                continue
            seen.add(key)
            cells.append({'row': row, 'col': col})

        if not cells:
            raise ValueError('Selection must include at least one cell')

        row_values = [cell['row'] for cell in cells]
        col_values = [cell['col'] for cell in cells]
        row_start = min(row_values)
        row_end = max(row_values)
        col_start = min(col_values)
        col_end = max(col_values)
        area = (row_end - row_start + 1) * (col_end - col_start + 1)
        is_rectangular = len(cells) == area
        return {
            'row_start': row_start,
            'row_end': row_end,
            'col_start': col_start,
            'col_end': col_end,
            'cells': cells,
            'is_rectangular': is_rectangular,
        }

    row_start = int(selection.get('row_start', selection.get('row', 0)))
    row_end = int(selection.get('row_end', row_start))
    col_start = int(selection.get('col_start', selection.get('col', 0)))
    col_end = int(selection.get('col_end', col_start))

    row_start, row_end = sorted((row_start, row_end))
    col_start, col_end = sorted((col_start, col_end))

    if row_start < 0 or row_end >= rows:
        raise ValueError(f'Row selection {row_start}:{row_end} outside table bounds 0:{rows - 1}')
    if col_start < 0 or col_end >= cols:
        raise ValueError(f'Column selection {col_start}:{col_end} outside table bounds 0:{cols - 1}')

    return {
        'row_start': row_start,
        'row_end': row_end,
        'col_start': col_start,
        'col_end': col_end,
        'is_rectangular': True,
    }


def sanitize_parameters(parameters: dict) -> dict:
    """Return a JSON-safe, trimmed copy of operation parameters."""
    clean = {}
    for key, value in (parameters or {}).items():
        if isinstance(value, (int, float, str, bool)) or value is None:
            clean[key] = value
    return clean


def normalize_paste_values(values: list[list[float]]) -> list[list[float]]:
    """Coerce a pasted 2D value block into finite floats."""
    if not isinstance(values, list):
        raise ValueError('Paste values must be a list of rows')
    clean_rows = []
    for row in values:
        if not isinstance(row, list):
            raise ValueError('Paste values must be a list of rows')
        clean_row = []
        for value in row:
            numeric = _coerce_float(value)
            if numeric is None:
                raise ValueError('Paste values must be numeric')
            clean_row.append(numeric)
        if clean_row:
            clean_rows.append(clean_row)
    return clean_rows


def apply_operation_to_grid(grid: list[list[float]], selection: dict, operation: str, parameters: dict) -> list[list[float]]:
    """Return a new grid after applying one structured operation."""
    op = normalize_operation_name(operation)
    value = _coerce_float(parameters.get('value')) if 'value' in parameters else None
    row_start = selection['row_start']
    row_end = selection['row_end']
    col_start = selection['col_start']
    col_end = selection['col_end']

    new_grid = deepcopy(grid)
    selected_cells = list(iter_selection_cells(selection))

    if op == 'set_cells':
        if value is None:
            raise ValueError('set_cells requires a numeric value parameter')
        for row, col in selected_cells:
            new_grid[row][col] = value
        return new_grid

    if op == 'add_constant':
        if value is None:
            raise ValueError('add_constant requires a numeric value parameter')
        for row, col in selected_cells:
            new_grid[row][col] = new_grid[row][col] + value
        return new_grid

    if op == 'multiply':
        if value is None:
            raise ValueError('multiply requires a numeric value parameter')
        for row, col in selected_cells:
            new_grid[row][col] = new_grid[row][col] * value
        return new_grid

    ensure_rectangular_selection(selection, op)

    if op == 'interpolate_x':
        if col_start == col_end:
            raise ValueError('interpolate_x requires at least two selected columns')
        for row in range(row_start, row_end + 1):
            left = grid[row][col_start]
            right = grid[row][col_end]
            span = col_end - col_start
            for col in range(col_start, col_end + 1):
                factor = (col - col_start) / span
                new_grid[row][col] = lerp(left, right, factor)
        return new_grid

    if op == 'interpolate_y':
        if row_start == row_end:
            raise ValueError('interpolate_y requires at least two selected rows')
        for col in range(col_start, col_end + 1):
            top = grid[row_start][col]
            bottom = grid[row_end][col]
            span = row_end - row_start
            for row in range(row_start, row_end + 1):
                factor = (row - row_start) / span
                new_grid[row][col] = lerp(top, bottom, factor)
        return new_grid

    if op == 'interpolate_xy':
        if row_start == row_end:
            return apply_operation_to_grid(grid, selection, 'interpolate_x', parameters)
        if col_start == col_end:
            return apply_operation_to_grid(grid, selection, 'interpolate_y', parameters)
        top_left = grid[row_start][col_start]
        top_right = grid[row_start][col_end]
        bottom_left = grid[row_end][col_start]
        bottom_right = grid[row_end][col_end]
        row_span = row_end - row_start
        col_span = col_end - col_start
        for row in range(row_start, row_end + 1):
            ty = (row - row_start) / row_span
            left = lerp(top_left, bottom_left, ty)
            right = lerp(top_right, bottom_right, ty)
            for col in range(col_start, col_end + 1):
                tx = (col - col_start) / col_span
                new_grid[row][col] = lerp(left, right, tx)
        return new_grid

    raise ValueError(f'Unsupported operation: {op}')


def summarize_changes(before_grid: list[list[float]], after_grid: list[list[float]]) -> list[dict]:
    """Return per-cell before/after deltas for changed cells."""
    changes = []
    for row_index, row in enumerate(before_grid):
        for col_index, before_value in enumerate(row):
            after_value = after_grid[row_index][col_index]
            if values_close(before_value, after_value):
                continue
            changes.append({
                'row': row_index,
                'col': col_index,
                'before': before_value,
                'after': after_value,
                'delta': after_value - before_value,
            })
    return changes


def build_preview_summary(changes: list[dict]) -> dict:
    """Return aggregate preview stats."""
    if not changes:
        return {
            'changed_cell_count': 0,
            'min_delta': 0.0,
            'max_delta': 0.0,
        }

    deltas = [change['delta'] for change in changes]
    return {
        'changed_cell_count': len(changes),
        'min_delta': min(deltas),
        'max_delta': max(deltas),
    }


def summarize_axis_changes(current_values: list[float], imported_values: list[float] | None) -> list[dict]:
    """Return changed indices for an imported axis value list."""
    if imported_values is None:
        return []
    changes = []
    for index, current in enumerate(current_values):
        imported = imported_values[index]
        if values_close(current, imported):
            continue
        changes.append({
            'index': index,
            'before': current,
            'after': imported,
            'delta': imported - current,
        })
    return changes


def drop_preview_grid(preview: dict) -> dict:
    """Strip heavy preview-only keys before returning apply responses."""
    result = dict(preview)
    result.pop('after_grid', None)
    return result


def parse_table_import_text(import_text: str, current_table: dict, *, mode: str = 'values_only') -> dict:
    """Parse pasted AI table text into values and optional axes."""
    clean_mode = normalize_import_mode(mode)
    text = strip_code_fences(import_text or '').strip()
    if not text:
        raise ValueError('Paste table text before previewing an import.')

    title = extract_import_title(text)
    x_values, y_values, value_lines = extract_import_sections(text)
    if not value_lines:
        value_lines = [line for line in text.splitlines() if line.strip()]

    grid = parse_grid_lines(
        value_lines,
        expected_rows=current_table['rows'],
        expected_cols=current_table['cols'],
    )

    if grid is None:
        raise ValueError('Could not parse any numeric table values from the imported text.')

    if len(grid) != current_table['rows'] or any(len(row) != current_table['cols'] for row in grid):
        raise ValueError(
            f"Imported values are {len(grid)}x{len(grid[0]) if grid else 0}, expected "
            f"{current_table['rows']}x{current_table['cols']} for {current_table['title']}."
        )

    imported_x = None
    imported_y = None
    if clean_mode == 'values_and_axes':
        if x_values is not None:
            if len(x_values) != len(current_table.get('x_labels') or []):
                raise ValueError(
                    f"Imported X axis has {len(x_values)} values, expected {len(current_table.get('x_labels') or [])}."
                )
            imported_x = x_values
        if y_values is not None:
            if len(y_values) != len(current_table.get('y_labels') or []):
                raise ValueError(
                    f"Imported Y axis has {len(y_values)} values, expected {len(current_table.get('y_labels') or [])}."
                )
            imported_y = y_values

    validation = {
        'rows': len(grid),
        'cols': len(grid[0]) if grid else 0,
        'expected_rows': current_table['rows'],
        'expected_cols': current_table['cols'],
        'x_axis_supplied': imported_x is not None,
        'y_axis_supplied': imported_y is not None,
        'x_axis_matches': imported_x is None or len(imported_x) == len(current_table.get('x_labels') or []),
        'y_axis_matches': imported_y is None or len(imported_y) == len(current_table.get('y_labels') or []),
    }
    return {
        'mode': clean_mode,
        'title': title,
        'grid': grid,
        'x_labels': imported_x,
        'y_labels': imported_y,
        'validation': validation,
    }


def normalize_import_mode(mode: str | None) -> str:
    """Normalize UI import modes to the supported backend values."""
    candidate = str(mode or 'values_only').strip().lower()
    aliases = {
        'values_only': 'values_only',
        'values': 'values_only',
        'grid_only': 'values_only',
        'values_and_axes': 'values_and_axes',
        'full': 'values_and_axes',
    }
    if candidate not in aliases:
        raise ValueError(f'Unsupported import mode: {mode}')
    return aliases[candidate]


def strip_code_fences(text: str) -> str:
    """Remove outer markdown code fences from pasted content."""
    stripped = text.strip()
    if stripped.startswith('```') and stripped.endswith('```'):
        lines = stripped.splitlines()
        if len(lines) >= 2:
            return '\n'.join(lines[1:-1])
    return text


def extract_import_title(text: str) -> str | None:
    """Extract a title from table import text when present."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('Title:'):
            return stripped.split(':', 1)[1].strip()
        lower = stripped.lower()
        if lower in {'[table import]', '[/table import]'}:
            continue
        if stripped.startswith('[') and stripped.endswith(']') and len(stripped) > 2:
            return stripped[1:-1].strip()
    return None


def extract_import_sections(text: str) -> tuple[list[float] | None, list[float] | None, list[str]]:
    """Extract optional X/Y axis sections and the values section."""
    current = None
    sections: dict[str, list[str]] = {'x': [], 'y': [], 'values': []}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if current == 'values':
                sections['values'].append('')
            continue
        lower = line.lower()
        if lower in {'[table import]', '[/table import]'}:
            continue
        if lower.startswith('mode:') or lower.startswith('title:') or lower.startswith('rows:') or lower.startswith('cols:'):
            continue
        if lower.startswith('x axis:'):
            current = 'x'
            remainder = line.split(':', 1)[1].strip()
            if remainder:
                sections['x'].append(remainder)
            continue
        if lower.startswith('y axis:'):
            current = 'y'
            remainder = line.split(':', 1)[1].strip()
            if remainder:
                sections['y'].append(remainder)
            continue
        if lower.startswith('values:'):
            current = 'values'
            remainder = line.split(':', 1)[1].strip()
            if remainder:
                sections['values'].append(remainder)
            continue
        if current in sections:
            sections[current].append(line)

    x_values = parse_axis_lines(sections['x']) if sections['x'] else None
    y_values = parse_axis_lines(sections['y']) if sections['y'] else None
    value_lines = sections['values']
    return x_values, y_values, value_lines


def parse_axis_lines(lines: list[str]) -> list[float]:
    """Parse X/Y axis breakpoint lines into a flat numeric list."""
    values: list[float] = []
    for line in lines:
        parts = [part.strip() for part in re.split(r'[,\|\t ]+', line) if part.strip()]
        for part in parts:
            values.append(float(part))
    return values


def parse_grid_lines(lines: list[str], *, expected_rows: int, expected_cols: int) -> list[list[float]] | None:
    """Parse grid text from CSV, pipe, whitespace, or Copy-for-AI style blocks."""
    rows: list[list[float]] = []
    scalar_next_value = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line or set(line) <= {'-', '+', '|'}:
            continue
        if line.startswith('[') and line.endswith(']'):
            continue
        if scalar_next_value:
            tokens = tokenize_import_line(line)
            numeric_tokens = [token for token in tokens if is_numeric_token(token)]
            if numeric_tokens:
                return [[float(numeric_tokens[0])]]
            scalar_next_value = False
        if line.lower().startswith('value:'):
            remainder = line.split(':', 1)[1].strip()
            if remainder:
                return [[float(tokenize_import_line(remainder)[0])]]
            scalar_next_value = True
            continue

        tokens = tokenize_import_line(line)
        if not tokens:
            continue

        numeric_flags = [is_numeric_token(token) for token in tokens]
        if expected_cols > 0 and len(tokens) == expected_cols + 1 and not numeric_flags[0]:
            if all(numeric_flags[1:]):
                rows.append([float(token) for token in tokens[1:]])
                continue
        if expected_cols > 0 and len(tokens) >= expected_cols:
            numeric_tokens = [token for token, numeric in zip(tokens, numeric_flags) if numeric]
            if len(numeric_tokens) == expected_cols:
                rows.append([float(token) for token in numeric_tokens])
                continue
        if all(numeric_flags):
            rows.append([float(token) for token in tokens])
    if not rows:
        return None
    if len(rows) == expected_rows + 1 and len(rows[0]) == expected_cols:
        rows = rows[1:]
    return rows


def tokenize_import_line(line: str) -> list[str]:
    """Split a possible table row into tokens while preserving numeric values."""
    work = line.strip().strip('|')
    if '|' in work:
        return [part.strip() for part in work.split('|') if part.strip()]
    if ',' in work:
        return [part.strip() for part in work.split(',') if part.strip()]
    return [part.strip() for part in re.split(r'\s+', work) if part.strip()]


def is_numeric_token(token: str) -> bool:
    """Return whether a token looks numeric."""
    try:
        float(token)
        return True
    except Exception:  # noqa: BLE001
        return False


def write_axis_values_to_bin(bin_buffer: bytearray, table_def: dict, axis_name: str,
                             scaled_values: list[float], *, changed_indices: list[int] | None = None) -> None:
    """Write scaled X or Y axis breakpoint values back into the BIN."""
    normalized_axis = normalize_axis_name(axis_name)
    axis = table_def.get(f'{normalized_axis}_axis')
    if axis is None or axis.get('embedded') is None:
        raise ValueError(f'{normalized_axis.upper()} axis is not writable for this table')

    if normalized_axis == 'x':
        rows, cols = 1, len(scaled_values)
    else:
        rows, cols = len(scaled_values), 1

    layout = get_axis_layout(
        axis,
        rows,
        cols,
        table_def.get('base_offset', 0),
        table_def.get('base_subtract', False),
    )
    if layout is None:
        raise ValueError(f'Unable to resolve {normalized_axis.upper()} axis layout')

    default_le = table_def.get('default_lsbfirst', False)
    default_signed = table_def.get('default_signed', False)
    equation = layout['math'] or 'X'
    indices = list(range(len(scaled_values))) if changed_indices is None else sorted(set(changed_indices))

    for index in indices:
        target_value = scaled_values[index]
        row_index = 0 if normalized_axis == 'x' else index
        col_index = index if normalized_axis == 'x' else 0
        raw_value = solve_raw_value(
            float(target_value),
            equation,
            layout['size_bits'],
            layout['type_flags'],
            default_signed=default_signed,
        )
        encoded = pack_raw_value(
            raw_value,
            layout['size_bits'],
            layout['type_flags'],
            default_le=default_le,
            default_signed=default_signed,
        )
        address = axis_cell_address(layout, row_index, col_index)
        end = address + len(encoded)
        if address < 0 or end > len(bin_buffer):
            raise ValueError(
                f'{normalized_axis.upper()} axis write would go out of range at 0x{address:x}..0x{end:x}'
            )
        bin_buffer[address:end] = encoded

    verified = extract_table(bytes(bin_buffer), table_def)
    verified_values = verified['x_labels'] if normalized_axis == 'x' else verified['y_labels']
    indices_to_check = range(len(scaled_values)) if changed_indices is None else indices
    for index in indices_to_check:
        if not axis_values_roundtrip_close([verified_values[index]], [scaled_values[index]]):
            raise ValueError(
                f'Round-trip verification failed for {normalized_axis.upper()} axis index {index} on {table_def.get("title", "")}'
            )


def write_grid_to_bin(bin_buffer: bytearray, table_def: dict, scaled_grid: list[list[float]]) -> None:
    """Write a scaled Z-axis grid back into a BIN buffer."""
    z_axis = table_def.get('z_axis')
    if z_axis is None or z_axis.get('embedded') is None:
        raise ValueError('Table does not have writable embedded Z-axis data')

    rows = len(scaled_grid)
    cols = len(scaled_grid[0]) if rows else 0
    if rows <= 0 or cols <= 0:
        raise ValueError('Cannot write an empty table grid')

    layout = get_axis_layout(
        z_axis,
        rows,
        cols,
        table_def.get('base_offset', 0),
        table_def.get('base_subtract', False),
    )
    if layout is None:
        raise ValueError('Unable to resolve axis layout')

    default_le = table_def.get('default_lsbfirst', False)
    default_signed = table_def.get('default_signed', False)
    equation = layout['math'] or 'X'

    for row_index in range(rows):
        for col_index in range(cols):
            target_value = float(scaled_grid[row_index][col_index])
            raw_value = solve_raw_value(
                target_value,
                equation,
                layout['size_bits'],
                layout['type_flags'],
                default_signed=default_signed,
            )
            encoded = pack_raw_value(
                raw_value,
                layout['size_bits'],
                layout['type_flags'],
                default_le=default_le,
                default_signed=default_signed,
            )
            address = axis_cell_address(layout, row_index, col_index)
            end = address + len(encoded)
            if address < 0 or end > len(bin_buffer):
                raise ValueError(
                    f'Write would go out of range at 0x{address:x}..0x{end:x} for table {table_def.get("title", "")}'
                )
            bin_buffer[address:end] = encoded

    verified = extract_table(bytes(bin_buffer), table_def)
    if not grid_roundtrip_close(
        verified['grid'],
        scaled_grid,
        equation=equation,
        size_bits=layout['size_bits'],
        type_flags=layout['type_flags'],
        default_signed=default_signed,
    ):
        raise ValueError(f"Round-trip verification failed for table {table_def.get('title', '')}")


def solve_raw_value(target_value: float, equation: str, size_bits: int, type_flags: int,
                    *, default_signed: bool = False) -> float:
    """Solve the raw BIN value that most closely represents the requested scaled value."""
    if not math.isfinite(target_value):
        raise ValueError('Target values must be finite')

    is_float = bool(type_flags & 0x04)
    if is_float:
        return solve_float_raw(target_value, equation)
    return solve_integer_raw(target_value, equation, size_bits, type_flags, default_signed=default_signed)


def solve_integer_raw(target_value: float, equation: str, size_bits: int, type_flags: int,
                      *, default_signed: bool = False) -> int:
    """Solve a discrete integer raw value using monotonic search plus refinement."""
    raw_min, raw_max = raw_numeric_range(size_bits, type_flags, default_signed)
    low_value = _apply_math(equation, float(raw_min))
    high_value = _apply_math(equation, float(raw_max))

    if not math.isfinite(low_value) or not math.isfinite(high_value):
        raise ValueError(f'Cannot invert non-finite equation result: {equation}')

    ascending = high_value >= low_value
    low = raw_min
    high = raw_max
    best_raw = low
    best_error = abs(low_value - target_value)

    if raw_max - raw_min <= 2048:
        return brute_force_raw(target_value, equation, raw_min, raw_max)

    while low <= high:
        mid = (low + high) // 2
        mid_value = _apply_math(equation, float(mid))
        if math.isfinite(mid_value):
            error = abs(mid_value - target_value)
            if error < best_error:
                best_error = error
                best_raw = mid

        if (mid_value < target_value and ascending) or (mid_value > target_value and not ascending):
            low = mid + 1
        else:
            high = mid - 1

    best_raw = brute_force_raw(
        target_value,
        equation,
        max(raw_min, best_raw - 12),
        min(raw_max, best_raw + 12),
        seed=best_raw,
    )
    best_value = _apply_math(equation, float(best_raw))
    local_resolution = estimate_resolution(best_raw, equation, raw_min, raw_max)
    allowed_error = max(local_resolution * 0.51, 1e-6)
    if abs(best_value - target_value) > allowed_error:
        raise ValueError(
            f'Could not round-trip target {target_value} with equation {equation}; closest is {best_value}'
        )
    return int(best_raw)


def solve_float_raw(target_value: float, equation: str) -> float:
    """Solve a float raw value using Newton steps with verification."""
    if not equation or equation.strip().lower() == 'x':
        return float(target_value)

    guess = float(target_value)
    epsilon = 1e-6
    for _ in range(24):
        current = _apply_math(equation, guess)
        delta = current - target_value
        if abs(delta) <= 1e-6:
            return guess
        derivative = (_apply_math(equation, guess + epsilon) - current) / epsilon
        if abs(derivative) <= 1e-12:
            break
        guess -= delta / derivative

    final = _apply_math(equation, guess)
    if abs(final - target_value) > 1e-4:
        raise ValueError(f'Could not invert float equation {equation} for target {target_value}')
    return guess


def brute_force_raw(target_value: float, equation: str, raw_min: int, raw_max: int, seed: int | None = None) -> int:
    """Search a discrete raw range and return the raw value with the smallest scaled error."""
    best_raw = seed if seed is not None else raw_min
    best_error = abs(_apply_math(equation, float(best_raw)) - target_value)
    for raw_value in range(raw_min, raw_max + 1):
        scaled = _apply_math(equation, float(raw_value))
        error = abs(scaled - target_value)
        if error < best_error:
            best_error = error
            best_raw = raw_value
    return int(best_raw)


def estimate_resolution(raw_value: int, equation: str, raw_min: int, raw_max: int) -> float:
    """Estimate the local scaled step size around a discrete raw value."""
    deltas = []
    if raw_value > raw_min:
        deltas.append(abs(_apply_math(equation, float(raw_value)) - _apply_math(equation, float(raw_value - 1))))
    if raw_value < raw_max:
        deltas.append(abs(_apply_math(equation, float(raw_value + 1)) - _apply_math(equation, float(raw_value))))
    finite_deltas = [delta for delta in deltas if math.isfinite(delta) and delta > 0]
    if finite_deltas:
        return min(finite_deltas)
    return 0.0


def normalize_axis_name(value: str) -> str:
    """Normalize axis identifiers used by the UI and API."""
    axis = str(value or '').strip().lower()
    if axis not in {'x', 'y'}:
        raise ValueError(f'Unsupported axis name: {value}')
    return axis


def table_entries_equal(left: dict, right: dict) -> bool:
    """Compare current vs source entries for dirty-state tracking."""
    if left.get('error') or right.get('error'):
        return left.get('error') == right.get('error')
    return grid_close(left.get('grid', []), right.get('grid', []))


def grid_close(left: list[list[float]], right: list[list[float]]) -> bool:
    """Compare two grids with float tolerance."""
    if len(left) != len(right):
        return False
    for left_row, right_row in zip(left, right):
        if len(left_row) != len(right_row):
            return False
        for left_value, right_value in zip(left_row, right_row):
            if not values_close(left_value, right_value):
                return False
    return True


def grid_roundtrip_close(left: list[list[float]], right: list[list[float]], *, equation: str = 'X',
                         size_bits: int | None = None, type_flags: int | None = None,
                         default_signed: bool = False) -> bool:
    """Compare two grids using table-aware tolerance for representable writeback steps."""
    if len(left) != len(right):
        return False
    for left_row, right_row in zip(left, right):
        if len(left_row) != len(right_row):
            return False
        for left_value, right_value in zip(left_row, right_row):
            if not representable_values_close(
                float(left_value),
                float(right_value),
                equation=equation,
                size_bits=size_bits,
                type_flags=type_flags,
                default_signed=default_signed,
            ):
                return False
    return True


def axis_values_roundtrip_close(left: list[float], right: list[float]) -> bool:
    """Compare one-dimensional axis values using writeback tolerance."""
    if len(left) != len(right):
        return False
    for left_value, right_value in zip(left, right):
        if not math.isclose(float(left_value), float(right_value), rel_tol=1e-6, abs_tol=5e-3):
            return False
    return True


def values_close(left: float, right: float) -> bool:
    """Return whether two numeric values are close enough to treat as equal."""
    return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-6)


def quantize_grid_region_for_display(grid: list[list[float]], selection: dict, decimal_places: int) -> list[list[float]]:
    """Round only the selected region to the table's display precision when it is explicit."""
    if decimal_places is None or decimal_places < 0:
        return deepcopy(grid)
    rounded = deepcopy(grid)
    for row, col in iter_selection_cells(selection):
        rounded[row][col] = round(float(rounded[row][col]), int(decimal_places))
    return rounded


def iter_selection_cells(selection: dict):
    """Yield selected cells for either rectangular or explicit-cell selections."""
    if selection.get('cells'):
        for cell in selection['cells']:
            yield int(cell['row']), int(cell['col'])
        return
    for row in range(selection['row_start'], selection['row_end'] + 1):
        for col in range(selection['col_start'], selection['col_end'] + 1):
            yield row, col


def ensure_rectangular_selection(selection: dict, operation: str) -> None:
    """Require contiguous rectangular selections for interpolation operations."""
    if selection.get('is_rectangular', True):
        return
    raise ValueError(f'{operation} requires a rectangular selection')


def representable_values_close(actual: float, target: float, *, equation: str = 'X',
                               size_bits: int | None = None, type_flags: int | None = None,
                               default_signed: bool = False) -> bool:
    """Return whether a verified value is acceptably close to the target for the storage format."""
    if values_close(actual, target):
        return True

    if size_bits is None or type_flags is None:
        return math.isclose(float(actual), float(target), rel_tol=1e-6, abs_tol=1e-3)

    is_float = bool(type_flags & 0x04)
    if is_float:
        return math.isclose(float(actual), float(target), rel_tol=1e-6, abs_tol=1e-4)

    raw_min, raw_max = raw_numeric_range(size_bits, type_flags, default_signed)
    nearest_raw = solve_integer_raw(
        float(target),
        equation or 'X',
        size_bits,
        type_flags,
        default_signed=default_signed,
    )
    resolution = estimate_resolution(nearest_raw, equation or 'X', raw_min, raw_max)
    allowed_error = max(resolution * 0.51, 1e-6)
    return math.isclose(float(actual), float(target), rel_tol=1e-6, abs_tol=allowed_error)


def lerp(start: float, end: float, factor: float) -> float:
    """Linear interpolation helper."""
    return float(start) + (float(end) - float(start)) * float(factor)


def _coerce_float(value) -> float:
    if value is None:
        raise ValueError('Expected a numeric parameter')
    return float(value)


def slugify(value: str) -> str:
    """Create a simple ASCII slug for IDs and filenames."""
    value = re.sub(r'[^a-zA-Z0-9]+', '-', str(value).strip().lower()).strip('-')
    return value or 'item'


def sanitize_stem(value: str) -> str:
    """Return a filesystem-safe filename stem."""
    cleaned = re.sub(r'[^a-zA-Z0-9._-]+', '-', str(value).strip()).strip('-')
    return cleaned or 'edited-bin'


def save_session_file(session: EditSession, path: str | Path) -> Path:
    """Write a session file to disk for CLI reuse."""
    path = Path(path)
    temp_path = path.with_name(f'{path.name}.tmp-{os.getpid()}')
    temp_path.write_text(json.dumps(session.to_payload(), indent=2), encoding='utf-8')
    os.replace(temp_path, path)
    return path


def load_session_file(path: str | Path) -> EditSession:
    """Read a session file from disk."""
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    return EditSession.from_payload(payload)
