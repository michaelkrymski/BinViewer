"""Agent-friendly CLI for the TunerPro BIN editor MVP."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import time
from collections import Counter, defaultdict

from editor_core import EditSession, load_session_file, save_session_file
import readonly_inspector


DEFAULT_SESSION_PATH = Path('tuner_session.json')
SESSION_LOCK_TIMEOUT_SECONDS = 30.0
SESSION_LOCK_POLL_SECONDS = 0.1


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI parser."""
    parser = argparse.ArgumentParser(
        description='Inspect, edit, diff, and export XDF/BIN sessions for the TunerPro editor MVP.',
    )
    parser.add_argument('--json', action='store_true', help='Print machine-readable JSON output')

    subparsers = parser.add_subparsers(dest='command', required=True)

    subparsers.add_parser('commands', help='List the available commands and what they do')
    describe_parser = subparsers.add_parser('describe', help='Describe commands, operations, selectors, and output shapes for agents')
    describe_parser.add_argument('--command-name', help='Optional command to describe in detail')

    parse_parser = subparsers.add_parser('parse', help='Create a session from an XDF and BIN')
    parse_parser.add_argument('xdf', help='Path to the XDF file')
    parse_parser.add_argument('bin', help='Path to the BIN file')
    parse_parser.add_argument('--bin2', help='Optional second BIN for diff mode')
    parse_parser.add_argument('--session', default=str(DEFAULT_SESSION_PATH), help='Session file to create/update')

    compare_parser = subparsers.add_parser('compare', help='Compare two BINs against one XDF without creating an edit session')
    compare_parser.add_argument('xdf', help='Path to the XDF file')
    compare_parser.add_argument('base_bin', help='Path to the base BIN file')
    compare_parser.add_argument('compare_bin', help='Path to the compare BIN file')
    compare_parser.add_argument('--limit', type=int, default=25, help='Maximum number of changed tables/raw ranges to print')

    list_parser = subparsers.add_parser('list', help='List tables and families in the current session')
    list_parser.add_argument('--session', default=str(DEFAULT_SESSION_PATH), help='Session file path')
    list_parser.add_argument('--bucket', help='Workflow bucket filter substring')
    list_parser.add_argument('--map', dest='map_name', help='Map label filter such as "Map 1"')
    list_parser.add_argument('--search', help='Title/family search filter')
    list_parser.add_argument('--limit', type=int, default=200, help='Maximum number of rows to print')

    maps_parser = subparsers.add_parser('maps', help='List detected maps, shared/global rows, and bucket counts in the current session')
    maps_parser.add_argument('--session', default=str(DEFAULT_SESSION_PATH), help='Session file path')

    families_parser = subparsers.add_parser('families', help='List family groups with map variants and member table ids')
    families_parser.add_argument('--session', default=str(DEFAULT_SESSION_PATH), help='Session file path')
    families_parser.add_argument('--map', dest='map_name', help='Map label filter such as "Map 1"')
    families_parser.add_argument('--search', help='Family/title filter')
    families_parser.add_argument('--limit', type=int, default=200, help='Maximum number of families to print')

    show_parser = subparsers.add_parser('show', help='Print one table with labels and values')
    show_parser.add_argument('table_id', nargs='?', help='Stable table_id from list output')
    show_parser.add_argument('--session', default=str(DEFAULT_SESSION_PATH), help='Session file path')
    add_target_selector_args(show_parser)

    diff_parser = subparsers.add_parser('diff', help='Summarize pending edits and dirty tables')
    diff_parser.add_argument('--session', default=str(DEFAULT_SESSION_PATH), help='Session file path')

    apply_parser = subparsers.add_parser('apply', help='Preview or apply one bounded edit operation')
    apply_parser.add_argument('table_id', nargs='?', help='Stable table_id from list output')
    apply_parser.add_argument('operation', help='set, add, multiply, interpolate-x, interpolate-y, interpolate-xy')
    apply_parser.add_argument('--session', default=str(DEFAULT_SESSION_PATH), help='Session file path')
    apply_parser.add_argument('--value', type=float, help='Numeric value for set/add/multiply')
    apply_parser.add_argument('--rows', help='1-based inclusive row selection, e.g. 2:5')
    apply_parser.add_argument('--cols', help='1-based inclusive column selection, e.g. 3:8')
    apply_parser.add_argument('--preview', action='store_true', help='Preview only; do not mutate the session')
    add_target_selector_args(apply_parser)

    axis_parser = subparsers.add_parser('axis', help='Preview or apply direct X/Y breakpoint edits')
    axis_parser.add_argument('table_id', nargs='?', help='Stable table_id from list output')
    axis_parser.add_argument('axis', help='Axis name: x or y')
    axis_parser.add_argument('--session', default=str(DEFAULT_SESSION_PATH), help='Session file path')
    axis_parser.add_argument(
        '--values',
        required=True,
        help='Comma-separated full axis values, e.g. 8.33,33.44,55,75,...',
    )
    axis_parser.add_argument('--preview', action='store_true', help='Preview only; do not mutate the session')
    add_target_selector_args(axis_parser)

    undo_parser = subparsers.add_parser('undo', help='Undo the most recent applied edit')
    undo_parser.add_argument('--session', default=str(DEFAULT_SESSION_PATH), help='Session file path')

    reset_parser = subparsers.add_parser('reset', help='Reset the draft back to the source BIN')
    reset_parser.add_argument('--session', default=str(DEFAULT_SESSION_PATH), help='Session file path')

    export_parser = subparsers.add_parser('export', help='Write the current draft BIN and change log to disk')
    export_parser.add_argument('output_bin', help='Output BIN path for the edited draft')
    export_parser.add_argument('--session', default=str(DEFAULT_SESSION_PATH), help='Session file path')

    batch_parser = subparsers.add_parser('batch', help='Run a JSON manifest of chained apply/axis/export steps')
    batch_parser.add_argument('manifest', help='Path to a JSON batch manifest')
    batch_parser.add_argument('--session', default=str(DEFAULT_SESSION_PATH), help='Session file path')

    inspect_parser = subparsers.add_parser('inspect', help='Open a read-only XDF/BIN workspace for AI or human review')
    inspect_subparsers = inspect_parser.add_subparsers(dest='inspect_action', required=True)
    inspect_open = inspect_subparsers.add_parser('open', help='Create a read-only workspace manifest for an XDF/BIN pair')
    inspect_open.add_argument('xdf', help='Path to XML/text XDF file')
    inspect_open.add_argument('bin', help='Path to BIN file')
    inspect_open.add_argument('--workspace', required=True, help='Output path for the inspection workspace manifest')
    inspect_subparsers.add_parser('guide', help='Print the model-neutral AI inspection guide')

    inspect_status = inspect_subparsers.add_parser('status', help='Show workspace source identity and table count')
    add_inspection_workspace_arg(inspect_status)
    inspect_maps = inspect_subparsers.add_parser('maps', help='Summarize maps, shared definitions, and categories')
    add_inspection_workspace_arg(inspect_maps)
    inspect_categories = inspect_subparsers.add_parser('categories', help='List category counts by map')
    add_inspection_workspace_arg(inspect_categories)
    inspect_categories.add_argument('--map', dest='map_name', help='Optional map filter')

    inspect_catalog = inspect_subparsers.add_parser('catalog', help='List XDF-defined tables using compact metadata')
    add_inspection_workspace_arg(inspect_catalog)
    inspect_catalog.add_argument('--map', dest='map_name', help='Filter to a map or Global')
    inspect_catalog.add_argument('--bucket', help='Filter workflow category, such as Boost or Fueling')
    inspect_catalog.add_argument('--family', help='Filter family name/key substring')
    inspect_catalog.add_argument('--search', help='Search titles, descriptions, categories, and families')
    inspect_catalog.add_argument('--limit', type=int, default=10000, help='Maximum entries to return (default: all)')

    inspect_search = inspect_subparsers.add_parser('search', help='Search table titles, descriptions, families, and categories')
    add_inspection_workspace_arg(inspect_search)
    inspect_search.add_argument('query', help='Search text')
    inspect_search.add_argument('--map', dest='map_name', help='Optional map filter')
    inspect_search.add_argument('--bucket', help='Optional workflow category filter')
    inspect_search.add_argument('--limit', type=int, default=100, help='Maximum matching entries')

    inspect_families = inspect_subparsers.add_parser('families', help='List multi-map table families and their members')
    add_inspection_workspace_arg(inspect_families)
    inspect_families.add_argument('--map', dest='map_name', help='Optional map filter')
    inspect_families.add_argument('--search', help='Family/title search')
    inspect_families.add_argument('--limit', type=int, default=10000, help='Maximum families to return')

    inspect_show = inspect_subparsers.add_parser('show', help='Read one table with complete axes and values')
    add_inspection_workspace_arg(inspect_show)
    inspect_show.add_argument('--table-id', help='Stable table identifier from catalog/families')
    inspect_show.add_argument('--title', help='Exact or substring title selector')
    inspect_show.add_argument('--family', help='Family selector; combine with --map to select a variant')
    inspect_show.add_argument('--map', dest='map_name', help='Map selector such as Map 2')

    inspect_compare = inspect_subparsers.add_parser('compare-maps', help='Compare every map variant in a family')
    add_inspection_workspace_arg(inspect_compare)
    inspect_compare.add_argument('--family', required=True, help='Family title or family key')
    inspect_compare.add_argument('--against-map', help='Baseline map (defaults to Map 1, else first map)')

    return parser


def add_target_selector_args(parser: argparse.ArgumentParser) -> None:
    """Add agent-friendly table target selectors to a command parser."""
    parser.add_argument('--title', help='Exact or substring title selector')
    parser.add_argument('--family', help='Exact or substring family selector')
    parser.add_argument('--map', dest='map_name', help='Map label such as "Map 1"')
    parser.add_argument('--bucket', help='Workflow bucket selector')


def add_inspection_workspace_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--workspace', required=True, help='Read-only inspection workspace manifest')


def emit(args, payload) -> int:
    """Print either JSON or readable text."""
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    command = getattr(args, 'command', '')
    if command == 'inspect':
        return emit_inspection(args, payload)
    if command == 'commands':
        print('commands')
        print('  describe Describe commands, operations, selectors, and output contracts')
        print('  inspect Read-only XDF/BIN discovery, table browsing, and map comparison')
        print('  parse   Create/update a session file from XDF + BIN inputs')
        print('  compare Compare two BINs with one XDF and summarize mapped + raw changes')
        print('  list    List tables with ids, buckets, dirty state, and dimensions')
        print('  maps    List detected maps, shared rows, and bucket counts')
        print('  families List family groups with map variants and member tables')
        print('  show    Print one table in a copy-for-AI style grid')
        print('  diff    Show pending edits and dirty tables')
        print('  apply   Preview or apply bounded edit operations')
        print('  axis    Preview or apply direct X/Y breakpoint edits')
        print('  undo    Undo the latest applied edit')
        print('  reset   Reset the draft to the original BIN')
        print('  export  Write the edited BIN plus companion change log')
        print('  batch   Execute a JSON manifest of chained steps')
        return 0

    if command == 'describe':
        if payload.get('command'):
            print(f"command: {payload['command']['name']}")
            print(payload['command']['summary'])
            print('selectors')
            for selector in payload['command'].get('selectors', []):
                print(f"  {selector}")
            print('arguments')
            for item in payload['command'].get('arguments', []):
                print(f"  {item}")
            return 0
        print('commands')
        for item in payload['commands']:
            print(f"  {item['name']}  {item['summary']}")
        print('operations')
        for op in payload['operations']:
            print(f"  {op['name']}  {op['summary']}")
        print('selectors')
        for selector in payload['selectors']:
            print(f"  {selector}")
        return 0

    if command == 'parse':
        print(f"session: {payload['session_path']}")
        print(f"tables: {payload['session']['dirty_table_count']} dirty / {payload['table_count']} total")
        print(f"source: {payload['session']['source_xdf_name']} + {payload['session']['source_bin_name']}")
        return 0

    if command == 'compare':
        summary = payload['summary']
        print(
            f"changed tables: {summary['changed_table_count']} | "
            f"raw ranges: {summary['raw_change_range_count']} | "
            f"source: {summary['base_bin_name']} -> {summary['compare_bin_name']}"
        )
        print('mapped changes')
        for table in payload['tables']:
            dims = f"{table['rows']}x{table['cols']}" if table['rows'] and table['cols'] else '-'
            print(f"  {table['table_id']} | {table['workflow_bucket']} | {dims} | {table['title']} | changed={table['changed_cell_count']}")
        print('raw ranges')
        for item in payload['raw_ranges']:
            print(
                f"  0x{item['start']:x}-0x{item['end']:x} | len={item['length']} | "
                f"before={item['before_hex']} | after={item['after_hex']}"
            )
        return 0

    if command == 'list':
        print(f"{len(payload['tables'])} tables")
        for table in payload['tables']:
            dims = f"{table['rows']}x{table['cols']}" if table['rows'] and table['cols'] else '-'
            dirty = 'dirty' if table.get('is_dirty') else 'clean'
            map_name = table.get('effective_map_variant') or table.get('map_variant') or ('Global' if table.get('is_shared_global') else '-')
            print(f"{table['table_id']} | {map_name} | {table['workflow_bucket']} | {dims} | {dirty} | {table['title']}")
        return 0

    if command == 'maps':
        print(f"maps: {len(payload['maps'])} | shared rows: {payload['shared_table_count']}")
        for item in payload['maps']:
            print(f"{item['map']} | tables={item['table_count']} | families={item['family_count']}")
            for bucket in item['buckets'][:6]:
                print(f"  {bucket['bucket']}: {bucket['table_count']}")
        return 0

    if command == 'families':
        print(f"{len(payload['families'])} families")
        for family in payload['families']:
            variants = ', '.join(family['maps']) if family['maps'] else 'Global only'
            print(f"{family['family_key']} | {family['family_title']} | maps={variants} | tables={family['table_count']}")
        return 0

    if command == 'show':
        if payload.get('resolved_target'):
            print(f"resolved: {payload['resolved_target']['table_id']} | {payload['resolved_target']['title']}")
        print(payload['formatted'])
        return 0

    if command == 'diff':
        summary = payload['session']
        print(
            f"pending edits: {summary['pending_change_count']} | "
            f"dirty tables: {summary['dirty_table_count']} | "
            f"export ready: {summary['export_ready']}"
        )
        for edit in payload['pending_edits']:
            print(
                f"{edit['timestamp']} | {edit['table_id']} | {edit['operation']} | "
                f"changed={edit['summary']['changed_cell_count']}"
            )
        return 0

    if command == 'apply':
        preview = payload['preview']
        summary = preview['summary']
        resolved = payload.get('resolved_target') or {'table_id': preview['table_id'], 'title': preview.get('table_title', preview['table_id'])}
        print(f"resolved: {resolved['table_id']} | {resolved['title']}")
        print(
            f"{preview['table_id']} {preview['operation']} | "
            f"changed={summary['changed_cell_count']} "
            f"delta=[{summary['min_delta']}, {summary['max_delta']}]"
        )
        if not args.preview:
            print(f"pending edits: {payload['session']['pending_change_count']}")
        return 0

    if command == 'axis':
        summary = payload['summary']
        resolved = payload.get('resolved_target') or {'table_id': payload['table_id'], 'title': payload['table_id']}
        print(f"resolved: {resolved['table_id']} | {resolved['title']}")
        print(
            f"{payload['table_id']} axis-{payload['axis']} | "
            f"changed={summary['changed_cell_count']} "
            f"delta=[{summary['min_delta']}, {summary['max_delta']}]"
        )
        if not args.preview:
            print(f"pending edits: {payload['session']['pending_change_count']}")
        return 0

    if command == 'undo':
        print(
            f"pending edits: {payload['session']['pending_change_count']} | "
            f"dirty tables: {payload['session']['dirty_table_count']}"
        )
        return 0

    if command == 'reset':
        print('draft reset to source BIN')
        return 0

    if command == 'export':
        print(f"bin: {payload['bin_path']}")
        print(f"log: {payload['log_path']}")
        return 0

    if command == 'batch':
        print(f"steps: {len(payload['steps'])}")
        for step in payload['steps']:
            status = step.get('status', 'ok')
            print(f"{step['index']}. {step['action']} | {status} | {step.get('message', '')}".rstrip())
        if payload.get('export'):
            print(f"bin: {payload['export']['bin_path']}")
            print(f"log: {payload['export']['log_path']}")
        return 0

    print(payload)
    return 0


def emit_inspection(args, payload) -> int:
    """Render read-only inspection output in concise text form."""
    action = args.inspect_action
    if action == 'open':
        workspace = payload['workspace']
        print(f"inspection workspace: {payload['workspace_path']}")
        print(f"tables: {workspace['table_count']}")
        print(f"XDF: {workspace['xdf']['path']} | sha256={workspace['xdf']['sha256']}")
        print(f"BIN: {workspace['bin']['path']} | sha256={workspace['bin']['sha256']}")
        print('Read-only inspection commands do not edit or export tune files.')
    elif action == 'guide':
        print(payload['guide'])
    elif action == 'status':
        workspace = payload['workspace']
        print(f"workspace: {workspace['workspace_id']} | tables={workspace['table_count']}")
        print(f"XDF: {workspace['xdf']['path']}")
        print(f"BIN: {workspace['bin']['path']}")
    elif action == 'maps':
        print(f"maps={len(payload['maps'])} | shared={payload['shared_table_count']} | total={payload['table_count']}")
        for item in payload['maps']:
            print(f"{item['map']} | tables={item['table_count']} | families={item['family_count']}")
            for bucket, count in item['workflow_buckets'].items():
                print(f"  {bucket}: {count}")
    elif action == 'categories':
        for item in payload['maps']:
            print(item['map'])
            print('  workflow: ' + ', '.join(f"{key}={value}" for key, value in item['workflow_buckets'].items()))
            print('  XDF: ' + ', '.join(f"{key}={value}" for key, value in item['xdf_categories'].items()))
    elif action in {'catalog', 'search'}:
        print(f"matched={payload['matched_count']} | returned={payload['returned_count']} | truncated={payload['truncated']}")
        for item in payload['tables']:
            dims = f"{item['rows']}x{item['cols']}"
            inferred = ' inferred' if item['map_inferred'] else ''
            print(f"{item['table_id']} | {item['map']}{inferred} | {item['category']} | {dims} | {item['family']} | {item['title']}")
    elif action == 'families':
        print(f"families={payload['family_count']} | returned={payload['returned_count']} | truncated={payload['truncated']}")
        for item in payload['families']:
            print(f"{item['family_key']} | {item['family_title']} | maps={', '.join(item['maps'])} | members={item['member_count']}")
    elif action == 'show':
        print(payload['formatted'])
    elif action == 'compare-maps':
        print(f"{payload['family_title']} | baseline={payload['baseline_map']} | maps={', '.join(payload['maps'])}")
        for variant in payload['variants']:
            print(f"  {variant['map']} | {variant['table_id']} | {variant.get('subrole') or 'main'} | {variant['title']} | {variant['dimensions']['rows']}x{variant['dimensions']['cols']}")
        for comparison in payload['comparisons']:
            baseline = comparison['baseline']
            target = comparison['target']
            if comparison['status'] == 'aligned':
                print(f"  {baseline['map']} {baseline['title']} -> {target['map']} {target['title']}: {comparison['changed_cell_count']} changed cells")
            else:
                print(f"  {baseline['title']} -> {target['title']}: {comparison['status']} (no cell deltas)")
    return 0


def load_required_session(path: str | Path) -> EditSession:
    """Load a session file or fail clearly."""
    session_path = Path(path)
    if not session_path.exists():
        raise FileNotFoundError(f'Session file not found: {session_path}')
    return load_session_file(session_path)


def get_table_map_name(table: dict) -> str | None:
    """Return the effective map label for a table when one exists."""
    return table.get('effective_map_variant') or table.get('map_variant') or None


def build_selector_dict(args) -> dict:
    """Extract target selector fields from argparse args."""
    return {
        'table_id': getattr(args, 'table_id', None),
        'title': getattr(args, 'title', None),
        'family': getattr(args, 'family', None),
        'map': getattr(args, 'map_name', None),
        'bucket': getattr(args, 'bucket', None),
    }


def selector_is_empty(selector: dict) -> bool:
    """Return whether no target selector fields were provided."""
    return not any(selector.get(key) for key in ('table_id', 'title', 'family', 'map', 'bucket'))


def resolve_target_table(session: EditSession, selector: dict) -> dict:
    """Resolve one table from table_id and/or friendly selectors."""
    tables = session.list_tables()
    candidates = tables

    table_id = selector.get('table_id')
    if table_id:
        candidates = [table for table in candidates if table['table_id'] == table_id]
        if not candidates:
            raise ValueError(f'No table found for table_id: {table_id}')

    title = selector.get('title')
    if title:
        exact = [table for table in candidates if str(table['title']).lower() == str(title).lower()]
        candidates = exact or [table for table in candidates if str(title).lower() in str(table['title']).lower()]

    family = selector.get('family')
    if family:
        exact = [table for table in candidates if str(table.get('family_title', '')).lower() == str(family).lower()]
        candidates = exact or [table for table in candidates if str(family).lower() in str(table.get('family_title', '')).lower()]

    map_name = selector.get('map')
    if map_name:
        map_query = str(map_name).lower()
        candidates = [
            table for table in candidates
            if (get_table_map_name(table) and str(get_table_map_name(table)).lower() == map_query)
            or (map_query == 'global' and table.get('is_shared_global'))
        ]

    bucket = selector.get('bucket')
    if bucket:
        bucket_query = str(bucket).lower()
        candidates = [table for table in candidates if bucket_query in str(table.get('workflow_bucket', '')).lower()]

    if not candidates:
        raise ValueError(f'No table matched selectors: {json.dumps(selector)}')

    candidates = prefer_primary_tables(candidates, selector)
    if len(candidates) > 1:
        sample = [
            {
                'table_id': item['table_id'],
                'title': item['title'],
                'family_title': item.get('family_title'),
                'map': get_table_map_name(item),
                'bucket': item.get('workflow_bucket'),
            }
            for item in candidates[:8]
        ]
        raise ValueError(f'Ambiguous table selectors: {json.dumps(sample, indent=2)}')
    return candidates[0]


def prefer_primary_tables(candidates: list[dict], selector: dict) -> list[dict]:
    """Prefer main data tables over axis/autogen companions when selectors are broad."""
    if len(candidates) <= 1:
        return candidates

    preferred = [item for item in candidates if not item.get('is_autogen')]
    if preferred:
        candidates = preferred

    family = selector.get('family')
    title = selector.get('title')
    if family or title:
        anchor = str(family or title).lower()
        exact_title = [item for item in candidates if str(item.get('title', '')).lower() == anchor]
        if exact_title:
            return exact_title
        main_family = [
            item for item in candidates
            if str(item.get('family_title', '')).lower() == anchor
            and str(item.get('title', '')).lower() == str(item.get('family_title', '')).lower()
        ]
        if main_family:
            return main_family

    non_axis = [
        item for item in candidates
        if ' - ' not in str(item.get('title', ''))
        and not str(item.get('title', '')).lower().endswith('(autogen)')
    ]
    if non_axis:
        return non_axis
    return candidates


def build_resolved_target_payload(table: dict) -> dict:
    """Return a compact target descriptor for agent-friendly output."""
    return {
        'table_id': table['table_id'],
        'title': table['title'],
        'family_title': table.get('family_title'),
        'map': get_table_map_name(table),
        'bucket': table.get('workflow_bucket'),
    }


@contextlib.contextmanager
def session_file_lock(path: str | Path, *, timeout_seconds: float = SESSION_LOCK_TIMEOUT_SECONDS):
    """Serialize CLI mutations for one session file using a simple lockfile."""
    session_path = Path(path)
    lock_path = session_path.with_name(f'{session_path.name}.lock')
    deadline = time.monotonic() + timeout_seconds
    fd = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, str(os.getpid()).encode('ascii', errors='ignore'))
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f'Timed out waiting for session lock: {lock_path}')
            time.sleep(SESSION_LOCK_POLL_SECONDS)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            lock_path.unlink()


def parse_selection(table: dict, rows_spec: str | None, cols_spec: str | None) -> dict:
    """Parse 1-based CLI ranges into 0-based editor selections."""
    row_start, row_end = parse_range(rows_spec, table['rows'])
    col_start, col_end = parse_range(cols_spec, table['cols'])
    return {
        'row_start': row_start,
        'row_end': row_end,
        'col_start': col_start,
        'col_end': col_end,
    }


def parse_range(spec: str | None, limit: int) -> tuple[int, int]:
    """Parse a 1-based inclusive range like 2:5."""
    if limit <= 0:
        return (0, 0)
    if not spec:
        return (0, limit - 1)
    if ':' not in spec:
        value = max(int(spec), 1)
        return (value - 1, value - 1)
    start_text, end_text = spec.split(':', 1)
    start = max(int(start_text), 1)
    end = max(int(end_text), 1)
    return (min(start, end) - 1, max(start, end) - 1)


def parse_axis_values(spec: str) -> list[float]:
    """Parse a comma-separated list of numeric axis values."""
    parts = [part.strip() for part in str(spec or '').split(',')]
    values = []
    for part in parts:
        if not part:
            continue
        values.append(float(part))
    if not values:
        raise ValueError('Axis edits require at least one numeric value')
    return values


def format_table(table: dict) -> str:
    """Render a table in the same text shape used by Copy for AI."""
    lines = []
    units_suffix = f" ({table['units']})" if table.get('units') else ''
    lines.append(f"[{table['title']}]{units_suffix}")

    if table.get('error'):
        lines.append(f"ERROR: {table['error']}")
        return '\n'.join(lines)

    if table['rows'] == 1 and table['cols'] == 1:
        lines.append(f"Value: {format_number(table['grid'][0][0])}{units_suffix}")
        return '\n'.join(lines)

    has_x = bool(table.get('x_labels'))
    has_y = bool(table.get('y_labels'))
    widths = [3] * (table['cols'] + 1)
    if has_y:
        widths[0] = max(3, *(len(format_number(value)) for value in table['y_labels']))

    for col in range(table['cols']):
        header = format_number(table['x_labels'][col]) if has_x and col < len(table['x_labels']) else f'C{col}'
        width = len(header)
        for row in range(table['rows']):
            width = max(width, len(format_number(table['grid'][row][col])))
        widths[col + 1] = width

    headers = [''.ljust(widths[0])]
    for col in range(table['cols']):
        header = format_number(table['x_labels'][col]) if has_x and col < len(table['x_labels']) else f'C{col}'
        headers.append(header.ljust(widths[col + 1]))
    lines.append(' | '.join(headers))
    lines.append('-+-'.join('-' * width for width in widths))

    for row in range(table['rows']):
        row_label = format_number(table['y_labels'][row]) if has_y and row < len(table['y_labels']) else f'R{row}'
        cells = [row_label.ljust(widths[0])]
        for col in range(table['cols']):
            cells.append(format_number(table['grid'][row][col]).ljust(widths[col + 1]))
        lines.append(' | '.join(cells))
    return '\n'.join(lines)


def format_number(value) -> str:
    """Match the web app's compact numeric formatting."""
    if not isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, bool):
        return str(value)
    if not float(value).is_integer():
        absolute = abs(float(value))
        if absolute >= 1000:
            decimals = 0
        elif absolute >= 100:
            decimals = 1
        elif absolute >= 1:
            decimals = 2
        elif absolute >= 0.1:
            decimals = 3
        else:
            decimals = 4
        return str(float(f'{float(value):.{decimals}f}'))
    return str(int(value))


def command_parse(args) -> dict:
    with session_file_lock(args.session):
        session = EditSession(
            xdf_bytes=Path(args.xdf).read_bytes(),
            bin_bytes=Path(args.bin).read_bytes(),
            xdf_name=Path(args.xdf).name,
            bin_name=Path(args.bin).name,
            bin2_bytes=Path(args.bin2).read_bytes() if args.bin2 else None,
            bin2_name=Path(args.bin2).name if args.bin2 else None,
        )
        session_path = save_session_file(session, args.session)
    return {
        'session_path': str(session_path),
        'table_count': len(session.bindings),
        'session': session.get_summary(),
    }


def command_describe(args) -> dict:
    """Describe commands, operations, selectors, and outputs for agent discovery."""
    commands = [
        {
            'name': 'parse',
            'summary': 'Create or refresh a session from XDF + BIN inputs.',
            'arguments': ['xdf', 'bin', '--bin2 (optional)', '--session'],
        },
        {
            'name': 'inspect',
            'summary': 'Open a read-only inspection workspace; see inspect guide for its browse commands.',
            'arguments': ['open', 'guide', 'status', 'maps', 'categories', 'catalog', 'search', 'families', 'show', 'compare-maps'],
        },
        {
            'name': 'compare',
            'summary': 'Compare two BINs with one XDF and return mapped + raw diffs.',
            'arguments': ['xdf', 'base_bin', 'compare_bin', '--limit'],
        },
        {
            'name': 'list',
            'summary': 'List session tables with ids, maps, buckets, dimensions, and dirty state.',
            'arguments': ['--session', '--bucket', '--map', '--search', '--limit'],
        },
        {
            'name': 'maps',
            'summary': 'List detected maps, shared rows, and bucket counts.',
            'arguments': ['--session'],
        },
        {
            'name': 'families',
            'summary': 'List families, map variants, and member table ids.',
            'arguments': ['--session', '--map', '--search', '--limit'],
        },
        {
            'name': 'show',
            'summary': 'Show one resolved table in a grid.',
            'arguments': ['[table_id]', '--session', '--title', '--family', '--map', '--bucket'],
            'selectors': ['table_id', 'title', 'family', 'map', 'bucket'],
        },
        {
            'name': 'apply',
            'summary': 'Preview or apply one bounded edit operation.',
            'arguments': ['[table_id]', 'operation', '--session', '--rows', '--cols', '--value', '--preview', '--title', '--family', '--map', '--bucket'],
            'selectors': ['table_id', 'title', 'family', 'map', 'bucket'],
        },
        {
            'name': 'axis',
            'summary': 'Preview or apply direct X/Y breakpoint edits.',
            'arguments': ['[table_id]', 'axis', '--values', '--session', '--preview', '--title', '--family', '--map', '--bucket'],
            'selectors': ['table_id', 'title', 'family', 'map', 'bucket'],
        },
        {
            'name': 'diff',
            'summary': 'Summarize pending edits and dirty tables in the current session.',
            'arguments': ['--session'],
        },
        {
            'name': 'undo',
            'summary': 'Undo the most recent applied edit.',
            'arguments': ['--session'],
        },
        {
            'name': 'reset',
            'summary': 'Reset the draft to the original BIN.',
            'arguments': ['--session'],
        },
        {
            'name': 'export',
            'summary': 'Write the current draft BIN and companion change log.',
            'arguments': ['output_bin', '--session'],
        },
        {
            'name': 'batch',
            'summary': 'Execute a chain of apply/axis/export steps from a JSON manifest.',
            'arguments': ['manifest', '--session'],
        },
    ]
    operations = [
        {'name': 'set', 'summary': 'Set selected cells to one numeric value.'},
        {'name': 'add', 'summary': 'Add a constant to the selected cells.'},
        {'name': 'multiply', 'summary': 'Multiply the selected cells by a scalar.'},
        {'name': 'interpolate-x', 'summary': 'Interpolate across the selected X span.'},
        {'name': 'interpolate-y', 'summary': 'Interpolate across the selected Y span.'},
        {'name': 'interpolate-xy', 'summary': 'Bilinear interpolate across the selected rectangle.'},
    ]
    selectors = [
        'table_id: exact stable identifier from list/families output',
        'title: exact or substring table title',
        'family: exact or substring family title',
        'map: map label such as "Map 1" or "Global"',
        'bucket: workflow bucket substring such as "Boost" or "Fueling"',
    ]
    output_shapes = {
        'show': ['table', 'formatted', 'resolved_target', 'session'],
        'apply-preview': ['preview', 'resolved_target', 'session'],
        'apply': ['preview', 'table', 'session', 'pending_edits', 'resolved_target'],
        'axis-preview': ['table_id', 'axis', 'changes', 'summary', 'resolved_target', 'session'],
        'compare': ['summary', 'tables', 'raw_ranges'],
        'maps': ['maps', 'shared_table_count', 'session'],
        'families': ['families', 'session'],
        'batch': ['steps', 'session', 'export?'],
    }
    if args.command_name:
        name = str(args.command_name).strip().lower()
        command = next((item for item in commands if item['name'] == name), None)
        if not command:
            raise ValueError(f'Unknown command for describe: {args.command_name}')
        return {'command': command, 'operations': operations, 'selectors': selectors, 'output_shapes': output_shapes}
    return {'commands': commands, 'operations': operations, 'selectors': selectors, 'output_shapes': output_shapes}


def command_inspect(args) -> dict:
    """Run a read-only inspection operation; this path never saves tune data."""
    action = args.inspect_action
    if action == 'open':
        return readonly_inspector.open_workspace(args.xdf, args.bin, args.workspace)
    if action == 'guide':
        guide_path = Path(__file__).resolve().parent / 'docs' / 'AI_READ_ONLY_INSPECTION.md'
        return {'guide_path': str(guide_path), 'guide': guide_path.read_text(encoding='utf-8')}

    manifest, session = readonly_inspector.load_workspace(args.workspace)
    workspace_summary = {
        'workspace_id': manifest['workspace_id'],
        'xdf': manifest['xdf'],
        'bin': manifest['bin'],
        'table_count': manifest['table_count'],
    }
    if action == 'status':
        return {'workspace': workspace_summary}
    if action == 'maps':
        return {'workspace': workspace_summary, **readonly_inspector.maps_summary(session)}
    if action == 'categories':
        return {'workspace': workspace_summary, **readonly_inspector.categories(session, map_name=args.map_name)}
    if action == 'catalog':
        result = readonly_inspector.catalog(
            session,
            map_name=args.map_name,
            bucket=args.bucket,
            family=args.family,
            search=args.search,
            limit=args.limit,
        )
        return {'workspace': workspace_summary, **result}
    if action == 'search':
        result = readonly_inspector.search_tables(
            session, args.query, map_name=args.map_name, bucket=args.bucket, limit=args.limit,
        )
        return {'workspace': workspace_summary, **result}
    if action == 'families':
        result = readonly_inspector.families(
            session, map_name=args.map_name, search=args.search, limit=args.limit,
        )
        return {'workspace': workspace_summary, **result}
    if action == 'show':
        result = readonly_inspector.show_table(
            session,
            table_id=args.table_id,
            title=args.title,
            family=args.family,
            map_name=args.map_name,
        )
        return {'workspace': workspace_summary, **result}
    if action == 'compare-maps':
        result = readonly_inspector.compare_maps(
            session, args.family, against_map=args.against_map,
        )
        return {'workspace': workspace_summary, **result}
    raise ValueError(f'Unsupported inspection command: {action}')


def command_compare(args) -> dict:
    session = EditSession(
        xdf_bytes=Path(args.xdf).read_bytes(),
        bin_bytes=Path(args.base_bin).read_bytes(),
        xdf_name=Path(args.xdf).name,
        bin_name=Path(args.base_bin).name,
        bin2_bytes=Path(args.compare_bin).read_bytes(),
        bin2_name=Path(args.compare_bin).name,
    )
    tables = []
    for table in session.list_tables():
        if not table.get('has_diff'):
            continue
        summary = summarize_table_diff(table)
        if summary['changed_cell_count'] == 0:
            continue
        entry = dict(table)
        entry.update(summary)
        tables.append(entry)
    raw_ranges = build_raw_diff_ranges(session.source_bin, session.reference_bin or b'')
    limit = max(args.limit, 0)
    return {
        'summary': {
            'xdf_name': session.source_xdf_name,
            'base_bin_name': session.source_bin_name,
            'compare_bin_name': session.reference_bin_name,
            'changed_table_count': len(tables),
            'raw_change_range_count': len(raw_ranges),
        },
        'tables': tables[:limit],
        'raw_ranges': raw_ranges[:limit],
    }


def command_list(args) -> dict:
    session = load_required_session(args.session)
    tables = session.list_tables()
    if args.bucket:
        query = args.bucket.lower()
        tables = [table for table in tables if query in table.get('workflow_bucket', '').lower()]
    if args.map_name:
        map_query = args.map_name.lower()
        tables = [
            table for table in tables
            if (get_table_map_name(table) and str(get_table_map_name(table)).lower() == map_query)
            or (map_query == 'global' and table.get('is_shared_global'))
        ]
    if args.search:
        query = args.search.lower()
        tables = [
            table for table in tables
            if query in table['title'].lower() or query in table.get('family_title', '').lower()
        ]
    tables = tables[:max(args.limit, 0)]
    return {'tables': tables, 'session': session.get_summary()}


def command_maps(args) -> dict:
    """List detected maps, shared tables, and bucket counts."""
    session = load_required_session(args.session)
    tables = session.list_tables()
    grouped: dict[str, list[dict]] = defaultdict(list)
    for table in tables:
        map_name = get_table_map_name(table)
        if map_name:
            grouped[map_name].append(table)
    result = []
    for map_name in sorted(grouped.keys()):
        bucket_counts = Counter(table.get('workflow_bucket', 'Other') for table in grouped[map_name])
        families = {table.get('family_key') or table.get('family_title') or table['title'] for table in grouped[map_name]}
        result.append({
            'map': map_name,
            'table_count': len(grouped[map_name]),
            'family_count': len(families),
            'buckets': [
                {'bucket': bucket, 'table_count': count}
                for bucket, count in sorted(bucket_counts.items(), key=lambda item: (-item[1], item[0]))
            ],
        })
    shared_count = sum(1 for table in tables if table.get('is_shared_global'))
    return {'maps': result, 'shared_table_count': shared_count, 'session': session.get_summary()}


def command_families(args) -> dict:
    """List family groups with map variants and member table ids."""
    session = load_required_session(args.session)
    tables = session.list_tables()
    if args.map_name:
        map_query = args.map_name.lower()
        tables = [
            table for table in tables
            if (get_table_map_name(table) and str(get_table_map_name(table)).lower() == map_query)
            or (map_query == 'global' and table.get('is_shared_global'))
        ]
    if args.search:
        query = args.search.lower()
        tables = [
            table for table in tables
            if query in table.get('family_title', '').lower() or query in table['title'].lower()
        ]
    grouped: dict[str, list[dict]] = defaultdict(list)
    for table in tables:
        key = table.get('family_key') or table.get('family_title') or table['title']
        grouped[key].append(table)
    families = []
    for family_key, members in grouped.items():
        members = sorted(members, key=lambda item: (get_table_map_name(item) or '', item['title']))
        maps = sorted({get_table_map_name(item) for item in members if get_table_map_name(item)})
        families.append({
            'family_key': family_key,
            'family_title': members[0].get('family_title') or members[0]['title'],
            'maps': maps,
            'table_count': len(members),
            'table_ids': [item['table_id'] for item in members],
            'bucket': members[0].get('workflow_bucket'),
        })
    families.sort(key=lambda item: item['family_title'])
    return {'families': families[:max(args.limit, 0)], 'session': session.get_summary()}


def command_show(args) -> dict:
    session = load_required_session(args.session)
    resolved = resolve_target_table(session, build_selector_dict(args))
    table = session.get_table(resolved['table_id'])
    return {'table': table, 'formatted': format_table(table), 'resolved_target': build_resolved_target_payload(resolved), 'session': session.get_summary()}


def command_diff(args) -> dict:
    session = load_required_session(args.session)
    return session.diff_summary()


def command_apply(args) -> dict:
    with session_file_lock(args.session):
        session = load_required_session(args.session)
        resolved = resolve_target_table(session, build_selector_dict(args))
        table = session.get_table(resolved['table_id'])
        selection = parse_selection(table, args.rows, args.cols)
        parameters = {}
        if args.value is not None:
            parameters['value'] = args.value

        if args.preview:
            preview = session.preview_operation(resolved['table_id'], args.operation, selection, parameters)
            return {'preview': preview, 'resolved_target': build_resolved_target_payload(resolved), 'session': session.get_summary()}

        result = session.apply_operation(resolved['table_id'], args.operation, selection, parameters)
        save_session_file(session, args.session)
        result['resolved_target'] = build_resolved_target_payload(resolved)
        return result


def command_axis(args) -> dict:
    values = parse_axis_values(args.values)
    with session_file_lock(args.session):
        session = load_required_session(args.session)
        resolved = resolve_target_table(session, build_selector_dict(args))
        table = session.get_table(resolved['table_id'])
        axis_name = str(args.axis or '').strip().lower()
        current_values = table['x_labels'] if axis_name == 'x' else table['y_labels'] if axis_name == 'y' else None
        if current_values is None:
            raise ValueError('Axis must be x or y')
        if len(values) != len(current_values):
            raise ValueError(f'{axis_name.upper()} axis expects {len(current_values)} values, got {len(values)}')

        changes = [
            {
                'index': index,
                'before': current_values[index],
                'after': values[index],
                'delta': values[index] - current_values[index],
            }
            for index in range(len(values))
            if abs(float(values[index]) - float(current_values[index])) > 1e-9
        ]
        summary = {
            'changed_cell_count': len(changes),
            'min_delta': min((change['delta'] for change in changes), default=0.0),
            'max_delta': max((change['delta'] for change in changes), default=0.0),
        }

        if args.preview:
            return {
                'table_id': resolved['table_id'],
                'axis': axis_name,
                'changes': changes[:160],
                'changes_truncated': len(changes) > 160,
                'summary': summary,
                'resolved_target': build_resolved_target_payload(resolved),
                'session': session.get_summary(),
            }

        result = session.apply_axis_edit(resolved['table_id'], axis_name, values)
        save_session_file(session, args.session)
        return {
            'table_id': resolved['table_id'],
            'axis': axis_name,
            'changes': result.get('changes', []),
            'changes_truncated': result.get('changes_truncated', False),
            'summary': result['pending_edits'][-1]['summary'] if result.get('pending_edits') else summary,
            'resolved_target': build_resolved_target_payload(resolved),
            'session': result['session'],
        }


def command_undo(args) -> dict:
    with session_file_lock(args.session):
        session = load_required_session(args.session)
        payload = session.undo_last()
        save_session_file(session, args.session)
        return payload


def command_reset(args) -> dict:
    with session_file_lock(args.session):
        session = load_required_session(args.session)
        session.reset_draft()
        save_session_file(session, args.session)
        return {'session': session.get_summary()}


def command_export(args) -> dict:
    with session_file_lock(args.session):
        session = load_required_session(args.session)
        return session.export_bin_and_log(args.output_bin)


def command_batch(args) -> dict:
    """Execute a sequence of chained edit steps from a JSON manifest."""
    manifest = json.loads(Path(args.manifest).read_text())
    steps = manifest.get('steps')
    if not isinstance(steps, list) or not steps:
        raise ValueError('Batch manifest requires a non-empty steps array')

    with session_file_lock(args.session):
        session = load_required_session(args.session)
        results = []
        export_result = None
        for index, step in enumerate(steps, start=1):
            action = str(step.get('action', '')).strip().lower()
            try:
                if action == 'apply':
                    resolved = resolve_target_table(session, step.get('target') or {})
                    table = session.get_table(resolved['table_id'])
                    selection = parse_selection(table, step.get('rows'), step.get('cols'))
                    params = {}
                    if 'value' in step:
                      params['value'] = step.get('value')
                    payload = session.apply_operation(resolved['table_id'], step.get('operation'), selection, params)
                    results.append({'index': index, 'action': action, 'status': 'ok', 'message': f"{resolved['title']} {step.get('operation')}", 'resolved_target': build_resolved_target_payload(resolved), 'summary': payload['preview']['summary']})
                elif action == 'axis':
                    resolved = resolve_target_table(session, step.get('target') or {})
                    payload = session.apply_axis_edit(resolved['table_id'], step.get('axis'), step.get('values') or [])
                    results.append({'index': index, 'action': action, 'status': 'ok', 'message': f"{resolved['title']} axis-{step.get('axis')}", 'resolved_target': build_resolved_target_payload(resolved), 'summary': payload['pending_edits'][-1]['summary'] if payload.get('pending_edits') else {}})
                elif action == 'export':
                    export_path = step.get('output_bin')
                    if not export_path:
                        raise ValueError('export step requires output_bin')
                    export_result = session.export_bin_and_log(export_path)
                    results.append({'index': index, 'action': action, 'status': 'ok', 'message': export_path})
                else:
                    raise ValueError(f'Unsupported batch action: {action}')
            except Exception as exc:  # noqa: BLE001
                results.append({'index': index, 'action': action or 'unknown', 'status': 'error', 'message': str(exc)})
                raise
        save_session_file(session, args.session)
    return {'steps': results, 'session': session.get_summary(), 'export': export_result}


def summarize_table_diff(table: dict) -> dict:
    changes = []
    grid = table.get('grid') or []
    grid2 = table.get('grid2') or []
    for row_index, row in enumerate(grid):
        other_row = grid2[row_index] if row_index < len(grid2) else []
        for col_index, value in enumerate(row):
            other = other_row[col_index] if col_index < len(other_row) else None
            if other is None or value != other:
                changes.append({
                    'row': row_index,
                    'col': col_index,
                    'before': value,
                    'after': other,
                })
    return {
        'changed_cell_count': len(changes),
        'changed_cells': changes[:80],
        'changes_truncated': len(changes) > 80,
    }


def build_raw_diff_ranges(base_bytes: bytes, compare_bytes: bytes) -> list[dict]:
    ranges = []
    max_len = min(len(base_bytes), len(compare_bytes))
    start = None
    for index in range(max_len):
        if base_bytes[index] != compare_bytes[index]:
            if start is None:
                start = index
            continue
        if start is not None:
            ranges.append(make_raw_range(base_bytes, compare_bytes, start, index))
            start = None
    if start is not None:
        ranges.append(make_raw_range(base_bytes, compare_bytes, start, max_len))
    return ranges


def make_raw_range(base_bytes: bytes, compare_bytes: bytes, start: int, end: int) -> dict:
    return {
        'start': start,
        'end': end,
        'length': end - start,
        'before_hex': base_bytes[start:end].hex(),
        'after_hex': compare_bytes[start:end].hex(),
    }


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == 'commands':
            payload = {'commands': ['describe', 'inspect', 'parse', 'compare', 'list', 'maps', 'families', 'show', 'diff', 'apply', 'axis', 'undo', 'reset', 'export', 'batch']}
        elif args.command == 'describe':
            payload = command_describe(args)
        elif args.command == 'inspect':
            payload = command_inspect(args)
        elif args.command == 'parse':
            payload = command_parse(args)
        elif args.command == 'compare':
            payload = command_compare(args)
        elif args.command == 'list':
            payload = command_list(args)
        elif args.command == 'maps':
            payload = command_maps(args)
        elif args.command == 'families':
            payload = command_families(args)
        elif args.command == 'show':
            payload = command_show(args)
        elif args.command == 'diff':
            payload = command_diff(args)
        elif args.command == 'apply':
            payload = command_apply(args)
        elif args.command == 'axis':
            payload = command_axis(args)
        elif args.command == 'undo':
            payload = command_undo(args)
        elif args.command == 'reset':
            payload = command_reset(args)
        elif args.command == 'export':
            payload = command_export(args)
        elif args.command == 'batch':
            payload = command_batch(args)
        else:
            parser.error(f'Unhandled command: {args.command}')
            return 2
    except Exception as exc:  # noqa: BLE001
        if args.json:
            print(json.dumps({'error': str(exc)}, indent=2))
        else:
            print(f'error: {exc}')
        return 1

    return emit(args, payload)


if __name__ == '__main__':
    raise SystemExit(main())
