"""Agent-friendly CLI for the TunerPro BIN editor MVP."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import time

from editor_core import EditSession, load_session_file, save_session_file


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

    parse_parser = subparsers.add_parser('parse', help='Create a session from an XDF and BIN')
    parse_parser.add_argument('xdf', help='Path to the XDF file')
    parse_parser.add_argument('bin', help='Path to the BIN file')
    parse_parser.add_argument('--bin2', help='Optional second BIN for diff mode')
    parse_parser.add_argument('--session', default=str(DEFAULT_SESSION_PATH), help='Session file to create/update')

    list_parser = subparsers.add_parser('list', help='List tables and families in the current session')
    list_parser.add_argument('--session', default=str(DEFAULT_SESSION_PATH), help='Session file path')
    list_parser.add_argument('--bucket', help='Workflow bucket filter substring')
    list_parser.add_argument('--search', help='Title/family search filter')
    list_parser.add_argument('--limit', type=int, default=200, help='Maximum number of rows to print')

    show_parser = subparsers.add_parser('show', help='Print one table with labels and values')
    show_parser.add_argument('table_id', help='Stable table_id from list output')
    show_parser.add_argument('--session', default=str(DEFAULT_SESSION_PATH), help='Session file path')

    diff_parser = subparsers.add_parser('diff', help='Summarize pending edits and dirty tables')
    diff_parser.add_argument('--session', default=str(DEFAULT_SESSION_PATH), help='Session file path')

    apply_parser = subparsers.add_parser('apply', help='Preview or apply one bounded edit operation')
    apply_parser.add_argument('table_id', help='Stable table_id from list output')
    apply_parser.add_argument('operation', help='set, add, multiply, interpolate-x, interpolate-y, interpolate-xy')
    apply_parser.add_argument('--session', default=str(DEFAULT_SESSION_PATH), help='Session file path')
    apply_parser.add_argument('--value', type=float, help='Numeric value for set/add/multiply')
    apply_parser.add_argument('--rows', help='1-based inclusive row selection, e.g. 2:5')
    apply_parser.add_argument('--cols', help='1-based inclusive column selection, e.g. 3:8')
    apply_parser.add_argument('--preview', action='store_true', help='Preview only; do not mutate the session')

    axis_parser = subparsers.add_parser('axis', help='Preview or apply direct X/Y breakpoint edits')
    axis_parser.add_argument('table_id', help='Stable table_id from list output')
    axis_parser.add_argument('axis', help='Axis name: x or y')
    axis_parser.add_argument('--session', default=str(DEFAULT_SESSION_PATH), help='Session file path')
    axis_parser.add_argument(
        '--values',
        required=True,
        help='Comma-separated full axis values, e.g. 8.33,33.44,55,75,...',
    )
    axis_parser.add_argument('--preview', action='store_true', help='Preview only; do not mutate the session')

    undo_parser = subparsers.add_parser('undo', help='Undo the most recent applied edit')
    undo_parser.add_argument('--session', default=str(DEFAULT_SESSION_PATH), help='Session file path')

    reset_parser = subparsers.add_parser('reset', help='Reset the draft back to the source BIN')
    reset_parser.add_argument('--session', default=str(DEFAULT_SESSION_PATH), help='Session file path')

    export_parser = subparsers.add_parser('export', help='Write the current draft BIN and change log to disk')
    export_parser.add_argument('output_bin', help='Output BIN path for the edited draft')
    export_parser.add_argument('--session', default=str(DEFAULT_SESSION_PATH), help='Session file path')

    return parser


def emit(args, payload) -> int:
    """Print either JSON or readable text."""
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    command = getattr(args, 'command', '')
    if command == 'commands':
        print('commands')
        print('  parse   Create/update a session file from XDF + BIN inputs')
        print('  list    List tables with ids, buckets, dirty state, and dimensions')
        print('  show    Print one table in a copy-for-AI style grid')
        print('  diff    Show pending edits and dirty tables')
        print('  apply   Preview or apply bounded edit operations')
        print('  axis    Preview or apply direct X/Y breakpoint edits')
        print('  undo    Undo the latest applied edit')
        print('  reset   Reset the draft to the original BIN')
        print('  export  Write the edited BIN plus companion change log')
        return 0

    if command == 'parse':
        print(f"session: {payload['session_path']}")
        print(f"tables: {payload['session']['dirty_table_count']} dirty / {payload['table_count']} total")
        print(f"source: {payload['session']['source_xdf_name']} + {payload['session']['source_bin_name']}")
        return 0

    if command == 'list':
        print(f"{len(payload['tables'])} tables")
        for table in payload['tables']:
            dims = f"{table['rows']}x{table['cols']}" if table['rows'] and table['cols'] else '-'
            dirty = 'dirty' if table.get('is_dirty') else 'clean'
            print(f"{table['table_id']} | {table['workflow_bucket']} | {dims} | {dirty} | {table['title']}")
        return 0

    if command == 'show':
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

    print(payload)
    return 0


def load_required_session(path: str | Path) -> EditSession:
    """Load a session file or fail clearly."""
    session_path = Path(path)
    if not session_path.exists():
        raise FileNotFoundError(f'Session file not found: {session_path}')
    return load_session_file(session_path)


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


def command_list(args) -> dict:
    session = load_required_session(args.session)
    tables = session.list_tables()
    if args.bucket:
        query = args.bucket.lower()
        tables = [table for table in tables if query in table.get('workflow_bucket', '').lower()]
    if args.search:
        query = args.search.lower()
        tables = [
            table for table in tables
            if query in table['title'].lower() or query in table.get('family_title', '').lower()
        ]
    tables = tables[:max(args.limit, 0)]
    return {'tables': tables, 'session': session.get_summary()}


def command_show(args) -> dict:
    session = load_required_session(args.session)
    table = session.get_table(args.table_id)
    return {'table': table, 'formatted': format_table(table), 'session': session.get_summary()}


def command_diff(args) -> dict:
    session = load_required_session(args.session)
    return session.diff_summary()


def command_apply(args) -> dict:
    with session_file_lock(args.session):
        session = load_required_session(args.session)
        table = session.get_table(args.table_id)
        selection = parse_selection(table, args.rows, args.cols)
        parameters = {}
        if args.value is not None:
            parameters['value'] = args.value

        if args.preview:
            preview = session.preview_operation(args.table_id, args.operation, selection, parameters)
            return {'preview': preview, 'session': session.get_summary()}

        result = session.apply_operation(args.table_id, args.operation, selection, parameters)
        save_session_file(session, args.session)
        return result


def command_axis(args) -> dict:
    values = parse_axis_values(args.values)
    with session_file_lock(args.session):
        session = load_required_session(args.session)
        table = session.get_table(args.table_id)
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
                'table_id': args.table_id,
                'axis': axis_name,
                'changes': changes[:160],
                'changes_truncated': len(changes) > 160,
                'summary': summary,
                'session': session.get_summary(),
            }

        result = session.apply_axis_edit(args.table_id, axis_name, values)
        save_session_file(session, args.session)
        return {
            'table_id': args.table_id,
            'axis': axis_name,
            'changes': result.get('changes', []),
            'changes_truncated': result.get('changes_truncated', False),
            'summary': result['pending_edits'][-1]['summary'] if result.get('pending_edits') else summary,
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


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == 'commands':
            payload = {'commands': ['parse', 'list', 'show', 'diff', 'apply', 'undo', 'reset', 'export']}
        elif args.command == 'parse':
            payload = command_parse(args)
        elif args.command == 'list':
            payload = command_list(args)
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
