"""Read-only inspection workspaces for XDF/BIN calibration files."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

from editor_core import EditSession


WORKSPACE_VERSION = 1


def open_workspace(xdf_path: str | Path, bin_path: str | Path, workspace_path: str | Path) -> dict:
    """Create a manifest pointing at immutable source files and validate the pair."""
    xdf = Path(xdf_path).expanduser().resolve(strict=True)
    bin_file = Path(bin_path).expanduser().resolve(strict=True)
    manifest_path = Path(workspace_path).expanduser().resolve()
    if manifest_path.exists():
        raise FileExistsError(f'Workspace path already exists; choose a new manifest path: {manifest_path}')
    if manifest_path in (xdf, bin_file):
        raise ValueError('Workspace manifest must be a separate file from the XDF and BIN inputs')

    xdf_bytes = xdf.read_bytes()
    bin_bytes = bin_file.read_bytes()
    session = EditSession(
        xdf_bytes=xdf_bytes,
        bin_bytes=bin_bytes,
        xdf_name=xdf.name,
        bin_name=bin_file.name,
    )
    manifest = {
        'version': WORKSPACE_VERSION,
        'workspace_id': uuid.uuid4().hex,
        'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'xdf': {'path': str(xdf), 'sha256': sha256_bytes(xdf_bytes), 'size': len(xdf_bytes)},
        'bin': {'path': str(bin_file), 'sha256': sha256_bytes(bin_bytes), 'size': len(bin_bytes)},
        'table_count': len(session.bindings),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return {'workspace': manifest, 'workspace_path': str(manifest_path)}


def load_workspace(workspace_path: str | Path) -> tuple[dict, EditSession]:
    """Load a workspace, rejecting missing or changed source files."""
    path = Path(workspace_path).expanduser().resolve(strict=True)
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('version') != WORKSPACE_VERSION:
        raise ValueError(f"Unsupported inspection workspace version: {manifest.get('version')}")

    xdf_bytes = read_verified_input(manifest.get('xdf') or {}, 'XDF')
    bin_bytes = read_verified_input(manifest.get('bin') or {}, 'BIN')
    session = EditSession(
        xdf_bytes=xdf_bytes,
        bin_bytes=bin_bytes,
        xdf_name=Path(manifest['xdf']['path']).name,
        bin_name=Path(manifest['bin']['path']).name,
    )
    return manifest, session


def read_verified_input(entry: dict, label: str) -> bytes:
    source_path = Path(str(entry.get('path') or '')).expanduser()
    if not source_path.is_file():
        raise ValueError(f'{label} input is missing: {source_path}')
    data = source_path.read_bytes()
    expected = entry.get('sha256')
    actual = sha256_bytes(data)
    if actual != expected:
        raise ValueError(
            f'{label} input changed since this inspection workspace was opened. '
            f'Reopen the workspace with the intended file: {source_path}'
        )
    return data


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def catalog(session: EditSession, *, map_name: str | None = None, bucket: str | None = None,
            family: str | None = None, search: str | None = None, limit: int = 10000) -> dict:
    tables = session.list_tables()
    filtered = filter_tables(tables, map_name=map_name, bucket=bucket, family=family, search=search)
    all_count = len(filtered)
    return {
        'table_count': len(tables),
        'matched_count': all_count,
        'returned_count': min(all_count, max(limit, 0)),
        'truncated': all_count > max(limit, 0),
        'tables': [catalog_entry(session, table) for table in filtered[:max(limit, 0)]],
    }


def categories(session: EditSession, *, map_name: str | None = None) -> dict:
    tables = session.list_tables()
    selected = filter_tables(tables, map_name=map_name)
    buckets: dict[str, Counter] = defaultdict(Counter)
    xdf_categories: dict[str, Counter] = defaultdict(Counter)
    for table in selected:
        map_label = map_label_for_table(table)
        buckets[map_label][table.get('workflow_bucket') or 'Other'] += 1
        for category in table.get('primary_category_path') or ['Uncategorised']:
            xdf_categories[map_label][category] += 1
    maps = []
    for label in sorted(set(buckets) | set(xdf_categories), key=map_sort_key):
        maps.append({
            'map': label,
            'workflow_buckets': dict(sorted(buckets[label].items())),
            'xdf_categories': dict(sorted(xdf_categories[label].items())),
        })
    return {'maps': maps}


def maps_summary(session: EditSession) -> dict:
    tables = session.list_tables()
    grouped: dict[str, list[dict]] = defaultdict(list)
    for table in tables:
        grouped[map_label_for_table(table)].append(table)
    result = []
    for name in sorted(grouped, key=map_sort_key):
        entries = grouped[name]
        families = {table.get('family_key') or table['table_id'] for table in entries}
        bucket_counts = Counter(table.get('workflow_bucket') or 'Other' for table in entries)
        result.append({
            'map': name,
            'table_count': len(entries),
            'family_count': len(families),
            'workflow_buckets': dict(sorted(bucket_counts.items())),
        })
    shared_count = sum(bool(table.get('is_shared_global')) for table in tables)
    return {'maps': result, 'shared_table_count': shared_count, 'table_count': len(tables)}


def families(session: EditSession, *, map_name: str | None = None, search: str | None = None,
             limit: int = 10000) -> dict:
    tables = filter_tables(session.list_tables(), map_name=map_name, search=search)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for table in tables:
        grouped[table.get('family_key') or table.get('family_title') or table['table_id']].append(table)
    result = []
    for key, members in grouped.items():
        members.sort(key=lambda table: (map_sort_key(map_label_for_table(table)), table.get('subrole') or '', table['title']))
        result.append({
            'family_key': key,
            'family_title': members[0].get('family_title') or members[0]['title'],
            'workflow_bucket': members[0].get('workflow_bucket') or 'Other',
            'maps': sorted({map_label_for_table(table) for table in members}, key=map_sort_key),
            'member_count': len(members),
            'members': [member_reference(table) for table in members],
        })
    result.sort(key=lambda family: (family['workflow_bucket'].lower(), family['family_title'].lower()))
    return {
        'family_count': len(result),
        'returned_count': min(len(result), max(limit, 0)),
        'truncated': len(result) > max(limit, 0),
        'families': result[:max(limit, 0)],
    }


def search_tables(session: EditSession, query: str, *, map_name: str | None = None,
                  bucket: str | None = None, limit: int = 100) -> dict:
    if not str(query or '').strip():
        raise ValueError('Search query cannot be empty')
    result = catalog(session, map_name=map_name, bucket=bucket, search=query, limit=limit)
    result['query'] = query
    return result


def show_table(session: EditSession, *, table_id: str | None = None, title: str | None = None,
               family: str | None = None, map_name: str | None = None) -> dict:
    table = resolve_table(session, table_id=table_id, title=title, family=family, map_name=map_name)
    definition = session._binding_by_id[table['table_id']].table_def
    return {
        'table': detailed_table(session, table, definition),
        'formatted': format_number_grid(table),
    }


def compare_maps(session: EditSession, family_query: str, *, against_map: str | None = None) -> dict:
    all_tables = session.list_tables()
    groups: dict[str, list[dict]] = defaultdict(list)
    for table in all_tables:
        groups[table.get('family_key') or table.get('family_title') or table['table_id']].append(table)
    selected_key, members = resolve_family(groups, family_query)
    mapped_members = [
        table for table in members
        if map_label_for_table(table) not in {'Global', 'Unassigned'} and not table.get('axis_suffix')
    ]
    if not mapped_members:
        raise ValueError(f'Family {family_query!r} has no map-specific members to compare')
    map_names = sorted({map_label_for_table(table) for table in mapped_members}, key=map_sort_key)
    baseline = against_map or ('Map 1' if 'Map 1' in map_names else map_names[0])
    baseline_members = [table for table in mapped_members if map_label_for_table(table).casefold() == baseline.casefold()]
    if not baseline_members:
        raise ValueError(f'Baseline {baseline!r} is not present. Available maps: {", ".join(map_names)}')

    comparisons = []
    for other_map in map_names:
        if other_map.casefold() == baseline.casefold():
            continue
        targets = [table for table in mapped_members if map_label_for_table(table) == other_map]
        for source in baseline_members:
            for target in targets:
                comparisons.append(compare_table_pair(source, target))

    return {
        'family_key': selected_key,
        'family_title': members[0].get('family_title') or members[0]['title'],
        'workflow_bucket': members[0].get('workflow_bucket') or 'Other',
        'baseline_map': baseline,
        'maps': map_names,
        'variants': [
            detailed_table(session, table, session._binding_by_id[table['table_id']].table_def)
            for table in sorted(mapped_members, key=lambda item: (map_sort_key(map_label_for_table(item)), item.get('subrole') or '', item['title']))
        ],
        'axis_companions': [
            member_reference(table)
            for table in members
            if table.get('axis_suffix')
        ],
        'comparisons': comparisons,
    }


def compare_table_pair(source: dict, target: dict) -> dict:
    result = {
        'baseline': member_reference(source),
        'target': member_reference(target),
        'status': 'aligned',
        'changed_cell_count': None,
        'changed_cells': [],
        'alignment_issues': [],
    }
    if source['rows'] != target['rows'] or source['cols'] != target['cols']:
        result['alignment_issues'].append({
            'kind': 'shape',
            'baseline': [source['rows'], source['cols']],
            'target': [target['rows'], target['cols']],
        })
    if source.get('x_labels') != target.get('x_labels'):
        result['alignment_issues'].append({'kind': 'x_axis', 'baseline': source.get('x_labels'), 'target': target.get('x_labels')})
    if source.get('y_labels') != target.get('y_labels'):
        result['alignment_issues'].append({'kind': 'y_axis', 'baseline': source.get('y_labels'), 'target': target.get('y_labels')})
    if result['alignment_issues']:
        result['status'] = 'not_aligned'
        return result

    changed = []
    diff_grid = []
    for row_index, (left_row, right_row) in enumerate(zip(source['grid'], target['grid'])):
        diff_row = []
        for col_index, (left, right) in enumerate(zip(left_row, right_row)):
            delta = right - left
            diff_row.append(delta)
            if delta != 0:
                changed.append({
                    'row': row_index,
                    'col': col_index,
                    'x': source.get('x_labels', [None] * source['cols'])[col_index] if col_index < len(source.get('x_labels') or []) else None,
                    'y': source.get('y_labels', [None] * source['rows'])[row_index] if row_index < len(source.get('y_labels') or []) else None,
                    'baseline_value': left,
                    'target_value': right,
                    'delta': delta,
                })
        diff_grid.append(diff_row)
    result.update({
        'changed_cell_count': len(changed),
        'changed_cells': changed,
        'delta_grid': diff_grid,
        'max_abs_delta': max((abs(item['delta']) for item in changed), default=0),
    })
    return result


def resolve_family(groups: dict[str, list[dict]], query: str) -> tuple[str, list[dict]]:
    folded = str(query or '').strip().casefold()
    if not folded:
        raise ValueError('A family name or key is required')
    exact = [(key, items) for key, items in groups.items()
             if key.casefold() == folded or str(items[0].get('family_title') or '').casefold() == folded]
    candidates = exact or [(key, items) for key, items in groups.items()
                           if folded in key.casefold() or folded in str(items[0].get('family_title') or '').casefold()]
    if len(candidates) != 1:
        names = sorted({str(items[0].get('family_title') or key) for key, items in candidates})
        if not candidates:
            raise ValueError(f'No family matches {query!r}')
        raise ValueError(f'Family selector {query!r} is ambiguous: {", ".join(names[:20])}')
    return candidates[0]


def resolve_table(session: EditSession, *, table_id: str | None = None, title: str | None = None,
                  family: str | None = None, map_name: str | None = None) -> dict:
    selectors = [value for value in (table_id, title, family) if value]
    if len(selectors) != 1:
        raise ValueError('Provide exactly one of --table-id, --title, or --family')
    tables = session.list_tables()
    candidates = tables
    if table_id:
        candidates = [table for table in tables if table['table_id'] == table_id]
    elif title:
        query = title.casefold()
        candidates = [table for table in tables if query in table.get('title', '').casefold()]
        exact = [table for table in candidates if table.get('title', '').casefold() == query]
        candidates = exact or candidates
    else:
        query = family.casefold()
        candidates = [table for table in tables if query in str(table.get('family_title') or '').casefold()
                      or query in str(table.get('family_key') or '').casefold()]
        exact = [table for table in candidates if str(table.get('family_title') or '').casefold() == query
                 or str(table.get('family_key') or '').casefold() == query]
        candidates = exact or candidates
    if map_name:
        candidates = [table for table in candidates if map_label_for_table(table).casefold() == map_name.casefold()]
    if family and map_name and len(candidates) > 1:
        primary = [table for table in candidates if not table.get('axis_suffix')]
        if primary:
            candidates = primary
        if len(candidates) > 1:
            largest_shape = max((table.get('rows', 0) * table.get('cols', 0) for table in candidates), default=0)
            largest = [table for table in candidates if table.get('rows', 0) * table.get('cols', 0) == largest_shape]
            if len(largest) == 1:
                candidates = largest
    if len(candidates) != 1:
        names = [f"{table['table_id']} ({map_label_for_table(table)}): {table['title']}" for table in candidates[:20]]
        if not candidates:
            raise ValueError('No table matches the supplied selector')
        raise ValueError('Table selector is ambiguous; narrow it with --map. Matches: ' + '; '.join(names))
    return candidates[0]


def filter_tables(tables: list[dict], *, map_name: str | None = None, bucket: str | None = None,
                  family: str | None = None, search: str | None = None) -> list[dict]:
    result = tables
    if map_name:
        query = map_name.casefold()
        result = [table for table in result if map_label_for_table(table).casefold() == query]
    if bucket:
        query = bucket.casefold()
        result = [table for table in result if query in str(table.get('workflow_bucket') or '').casefold()]
    if family:
        query = family.casefold()
        result = [table for table in result if query in str(table.get('family_title') or '').casefold()
                  or query in str(table.get('family_key') or '').casefold()]
    if search:
        query = search.casefold()
        result = [table for table in result if query in ' '.join([
            str(table.get('title') or ''), str(table.get('description') or ''),
            str(table.get('family_title') or ''), ' '.join(table.get('primary_category_path') or []),
            str(table.get('workflow_bucket') or ''),
        ]).casefold()]
    result.sort(key=lambda table: (
        str(table.get('workflow_bucket') or '').casefold(),
        map_sort_key(map_label_for_table(table)),
        str(table.get('family_title') or '').casefold(),
        str(table.get('title') or '').casefold(),
        table['table_id'],
    ))
    return result


def map_label_for_table(table: dict) -> str:
    return str(table.get('effective_map_variant') or table.get('map_variant') or
               ('Global' if table.get('is_shared_global') else 'Unassigned'))


def map_sort_key(value: str) -> tuple[int, str]:
    text = str(value)
    if text.startswith('Map '):
        try:
            return int(text.split()[1]), text
        except (ValueError, IndexError):
            pass
    return 99, text.casefold()


def member_reference(table: dict) -> dict:
    return {
        'table_id': table['table_id'],
        'title': table['title'],
        'map': map_label_for_table(table),
        'map_inferred': bool(table.get('map_inferred')),
        'map_assignment_reason': table.get('map_assignment_reason'),
        'subrole': table.get('subrole'),
        'axis_suffix': table.get('axis_suffix') or None,
        'variant_role': 'axis_companion' if table.get('axis_suffix') else 'primary_or_standalone',
        'rows': table.get('rows', 0),
        'cols': table.get('cols', 0),
    }


def catalog_entry(session: EditSession, table: dict) -> dict:
    definition = session._binding_by_id[table['table_id']].table_def
    z_axis = definition.get('z_axis') or {}
    return {
        'table_id': table['table_id'],
        'title': table['title'],
        'type': table.get('type'),
        'category': table.get('workflow_bucket') or 'Other',
        'xdf_category_path': table.get('primary_category_path') or ['Uncategorised'],
        'family_key': table.get('family_key'),
        'family': table.get('family_title'),
        'map': map_label_for_table(table),
        'map_inferred': bool(table.get('map_inferred')),
        'map_assignment_reason': table.get('map_assignment_reason'),
        'map_topology': table.get('map_topology'),
        'subrole': table.get('subrole'),
        'axis_suffix': table.get('axis_suffix') or None,
        'variant_role': 'axis_companion' if table.get('axis_suffix') else 'primary_or_standalone',
        'rows': table.get('rows', 0),
        'cols': table.get('cols', 0),
        'units': table.get('units') or z_axis.get('units') or '',
        'x_axis': axis_summary(definition.get('x_axis'), table.get('x_labels') or [], include_values=False),
        'y_axis': axis_summary(definition.get('y_axis'), table.get('y_labels') or [], include_values=False),
        'description': table.get('description_short') or table.get('description') or '',
        'xdf_address': (z_axis.get('embedded') or {}).get('address'),
        'element_size_bits': (z_axis.get('embedded') or {}).get('element_size_bits'),
        'is_autogen': bool(table.get('is_autogen')),
        'read_error': table.get('error'),
    }


def axis_summary(definition: dict | None, values: list, *, include_values: bool = True) -> dict | None:
    if not definition and not values:
        return None
    numeric = [float(value) for value in values if isinstance(value, (float, int))]
    result = {
        'label': (definition or {}).get('label') or '',
        'units': (definition or {}).get('units') or '',
        'count': len(values),
        'min': min(numeric) if numeric else None,
        'max': max(numeric) if numeric else None,
        'first': values[0] if values else None,
        'last': values[-1] if values else None,
    }
    if include_values:
        result['values'] = list(values)
    return result


def detailed_table(session: EditSession, table: dict, definition: dict) -> dict:
    z_axis = definition.get('z_axis') or {}
    return {
        'table_id': table['table_id'],
        'title': table['title'],
        'type': table.get('type'),
        'category': table.get('workflow_bucket') or 'Other',
        'xdf_category_path': table.get('primary_category_path') or ['Uncategorised'],
        'family_key': table.get('family_key'),
        'family': table.get('family_title'),
        'map': map_label_for_table(table),
        'map_inferred': bool(table.get('map_inferred')),
        'map_assignment_reason': table.get('map_assignment_reason'),
        'map_topology': table.get('map_topology'),
        'subrole': table.get('subrole'),
        'dimensions': {'rows': table.get('rows', 0), 'cols': table.get('cols', 0)},
        'units': table.get('units') or z_axis.get('units') or '',
        'x_axis': axis_summary(definition.get('x_axis'), table.get('x_labels') or []),
        'y_axis': axis_summary(definition.get('y_axis'), table.get('y_labels') or []),
        'values': table.get('grid') or [],
        'description': table.get('description_short') or table.get('description') or '',
        'description_long': table.get('description_long') or '',
        'xdf_address': (z_axis.get('embedded') or {}).get('address'),
        'element_size_bits': (z_axis.get('embedded') or {}).get('element_size_bits'),
        'read_error': table.get('error'),
    }


def format_number_grid(table: dict) -> str:
    lines = [f"[{table['title']}] | {map_label_for_table(table)} | {table.get('workflow_bucket') or 'Other'}"]
    lines.append(f"dimensions: {table.get('rows', 0)}x{table.get('cols', 0)} | units: {table.get('units') or '(none)'}")
    if table.get('error'):
        return '\n'.join(lines + [f"read error: {table['error']}"])
    rows = table.get('grid') or []
    x_axis = table.get('x_labels') or []
    y_axis = table.get('y_labels') or []
    if len(rows) == 1 and len(rows[0]) == 1:
        return '\n'.join(lines + [f'value: {format_inspect_number(rows[0][0])}'])
    lines.append('X axis: ' + ', '.join(format_inspect_number(value) for value in x_axis))
    lines.append('Y axis: ' + ', '.join(format_inspect_number(value) for value in y_axis))
    for index, row in enumerate(rows):
        row_label = format_inspect_number(y_axis[index]) if index < len(y_axis) else f'row {index + 1}'
        lines.append(f"{row_label}: " + ', '.join(format_inspect_number(value) for value in row))
    return '\n'.join(lines)


def format_inspect_number(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if float(value).is_integer():
        return str(int(value))
    absolute = abs(float(value))
    decimals = 0 if absolute >= 1000 else 1 if absolute >= 100 else 2 if absolute >= 1 else 3 if absolute >= 0.1 else 4
    return str(float(f'{float(value):.{decimals}f}'))
