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
                session.apply_operation(
                    edit['table_id'],
                    edit['operation'],
                    edit['selection'],
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

    def export_bin_and_log(self, output_path: str | Path) -> dict:
        """Write the draft BIN and a companion JSON change log to disk."""
        output_path = Path(output_path)
        output_path.write_bytes(bytes(self.working_bin))
        log_path = output_path.with_suffix(output_path.suffix + '.changes.json')
        log_path.write_text(
            json.dumps(
                {
                    'session': self.get_summary(),
                    'pending_edits': self.pending_edits,
                },
                indent=2,
            ),
            encoding='utf-8',
        )
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
    """Normalize and validate a rectangular selection."""
    if rows <= 0 or cols <= 0:
        raise ValueError('Selection is only available for non-empty tables')

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
    }


def sanitize_parameters(parameters: dict) -> dict:
    """Return a JSON-safe, trimmed copy of operation parameters."""
    clean = {}
    for key, value in (parameters or {}).items():
        if isinstance(value, (int, float, str, bool)) or value is None:
            clean[key] = value
    return clean


def apply_operation_to_grid(grid: list[list[float]], selection: dict, operation: str, parameters: dict) -> list[list[float]]:
    """Return a new grid after applying one structured operation."""
    op = normalize_operation_name(operation)
    value = _coerce_float(parameters.get('value')) if 'value' in parameters else None
    row_start = selection['row_start']
    row_end = selection['row_end']
    col_start = selection['col_start']
    col_end = selection['col_end']

    new_grid = deepcopy(grid)

    if op == 'set_cells':
        if value is None:
            raise ValueError('set_cells requires a numeric value parameter')
        for row in range(row_start, row_end + 1):
            for col in range(col_start, col_end + 1):
                new_grid[row][col] = value
        return new_grid

    if op == 'add_constant':
        if value is None:
            raise ValueError('add_constant requires a numeric value parameter')
        for row in range(row_start, row_end + 1):
            for col in range(col_start, col_end + 1):
                new_grid[row][col] = new_grid[row][col] + value
        return new_grid

    if op == 'multiply':
        if value is None:
            raise ValueError('multiply requires a numeric value parameter')
        for row in range(row_start, row_end + 1):
            for col in range(col_start, col_end + 1):
                new_grid[row][col] = new_grid[row][col] * value
        return new_grid

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


def drop_preview_grid(preview: dict) -> dict:
    """Strip heavy preview-only keys before returning apply responses."""
    result = dict(preview)
    result.pop('after_grid', None)
    return result


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
    for row in range(selection['row_start'], selection['row_end'] + 1):
        for col in range(selection['col_start'], selection['col_end'] + 1):
            rounded[row][col] = round(float(rounded[row][col]), int(decimal_places))
    return rounded


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
    path.write_text(json.dumps(session.to_payload(), indent=2), encoding='utf-8')
    return path


def load_session_file(path: str | Path) -> EditSession:
    """Read a session file from disk."""
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    return EditSession.from_payload(payload)
