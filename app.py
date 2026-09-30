"""
TunerPro XDF/BIN Viewer Flask backend.
"""
from io import BytesIO
import json
from pathlib import Path
import secrets
import traceback
from flask import Flask, jsonify, redirect, render_template, request, send_file, session as flask_session, url_for

from bin_reader import extract_table
from editor_core import EditSession, SessionRegistry
from log_overlay import build_log_overlay, infer_axis_mapping, parse_log_csv
from preferences_store import load_preferences, save_preferences
from project_store import (
    ProjectStore,
    build_project_export_path,
    get_project_draft_path,
    list_project_bins,
    resolve_project_paths,
    serialize_store_for_api,
)
from table_ranker import build_table_metadata_list
from xdf_parser import XdfFormatError, parse_xdf

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024
SESSIONS = SessionRegistry()
SESSION_LOGS: dict[str, dict] = {}
PROJECTS = ProjectStore()
LAN_ACCESS = {
    'enabled': False,
    'pin': '',
    'auth_token': '',
}


LAN_AUTH_PAGE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>BIN Viewer Access</title></head>
<body style="font-family:Arial,sans-serif;max-width:420px;margin:12vh auto;padding:0 20px;color:#1f2933;">
  <h1>Enter host PIN</h1>
  <p>This BIN Viewer session is being hosted from the Windows tray app.</p>
  <form method="post" action="/api/lan-auth">
    <input name="pin" inputmode="numeric" autocomplete="one-time-code" placeholder="PIN" style="font-size:20px;padding:10px;width:100%;box-sizing:border-box;margin-bottom:12px;">
    <button type="submit" style="font-size:16px;padding:10px 14px;">Join Session</button>
  </form>
</body>
</html>"""


def configure_lan_access(*, enabled: bool, pin: str | None = None) -> dict:
    """Enable or disable PIN auth for non-local clients."""
    LAN_ACCESS['enabled'] = bool(enabled)
    LAN_ACCESS['pin'] = str(pin or '') if enabled else ''
    LAN_ACCESS['auth_token'] = secrets.token_hex(16) if enabled else ''
    return get_hosting_status()


def get_hosting_status() -> dict:
    return {
        'lan_auth_enabled': LAN_ACCESS['enabled'],
        'pin': LAN_ACCESS['pin'] if LAN_ACCESS['enabled'] else '',
    }


@app.before_request
def require_lan_pin():
    if not LAN_ACCESS['enabled'] or _is_local_request():
        return None
    if request.endpoint in {'api_lan_auth', 'static'}:
        return None
    if flask_session.get('lan_auth_token') == LAN_ACCESS['auth_token']:
        return None
    if request.path.startswith('/api/'):
        return jsonify({'error': 'LAN PIN required'}), 401
    return LAN_AUTH_PAGE, 401


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/compare')
def compare():
    return render_template('compare.html')


@app.route('/api/lan-auth', methods=['POST'])
def api_lan_auth():
    payload = request.get_json(silent=True) or {}
    pin = str(payload.get('pin') or request.form.get('pin') or '').strip()
    if LAN_ACCESS['enabled'] and pin == LAN_ACCESS['pin']:
        flask_session['lan_auth_token'] = LAN_ACCESS['auth_token']
        if request.form:
            return redirect(url_for('index'))
        return jsonify({'ok': True})
    if request.form:
        return LAN_AUTH_PAGE, 403
    return jsonify({'error': 'Invalid PIN'}), 403


def serialize_session(session):
    log_state = SESSION_LOGS.get(session.id)
    return {
        'tables': session.list_tables(),
        'count': len(session.bindings),
        'categories': {str(k): v for k, v in session.categories_map.items()},
        'session': session.get_summary(),
        'preferences': load_preferences(),
        'log': _serialize_log_state(log_state) if log_state else None,
        'project': serialize_store_for_api(PROJECTS.load()),
    }


def require_session(session_id):
    try:
        return SESSIONS.get(session_id)
    except KeyError as exc:
        return jsonify({'error': str(exc)}), 404


@app.route('/api/parse', methods=['POST'])
def api_parse():
    try:
        xdf_file = request.files.get('xdf')
        bin_file = request.files.get('bin')

        if not xdf_file:
            return jsonify({'error': 'XDF file is required'}), 400
        if not bin_file:
            return jsonify({'error': 'BIN file is required'}), 400

        xdf_bytes = xdf_file.read()
        bin_data = bin_file.read()

        session = SESSIONS.create(
            xdf_bytes=xdf_bytes,
            bin_bytes=bin_data,
            xdf_name=xdf_file.filename or 'input.xdf',
            bin_name=bin_file.filename or 'input.bin',
        )
        return jsonify(serialize_session(session))

    except XdfFormatError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 500


@app.route('/api/debug', methods=['POST'])
def api_debug():
    try:
        xdf_file = request.files.get('xdf')
        bin_file = request.files.get('bin')
        if not xdf_file:
            return jsonify({'error': 'XDF file is required'}), 400

        xdf_bytes = xdf_file.read()
        bin_data = bin_file.read() if bin_file else None

        table_defs, categories_map = parse_xdf(xdf_bytes)
        out = []
        metadata_list = build_table_metadata_list(table_defs, categories_map)
        for td, metadata in zip(table_defs, metadata_list):
            z = td.get('z_axis') or {}
            x = td.get('x_axis') or {}
            y = td.get('y_axis') or {}
            z_emb = z.get('embedded') or {}
            x_emb = x.get('embedded') or {}
            y_emb = y.get('embedded') or {}

            sample_hex = None
            if bin_data and z_emb:
                addr = z_emb.get('address', 0)
                base_offset = td.get('base_offset', 0)
                base_subtract = td.get('base_subtract', False)
                file_addr = (addr - base_offset) if base_subtract else (addr + base_offset)
                size_bytes = max(z_emb.get('element_size_bits', 8) // 8, 1)
                if 0 <= file_addr < len(bin_data):
                    chunk = bin_data[file_addr:file_addr + size_bytes]
                    sample_hex = chunk.hex()
                else:
                    sample_hex = f'OUT OF RANGE (file_addr=0x{file_addr:x}, bin_size=0x{len(bin_data):x})'

            out.append({
                'title': td['title'],
                'type': td['type'],
                'base_offset': td.get('base_offset', 0),
                'base_subtract': td.get('base_subtract', False),
                'z_address_hex': hex(z_emb.get('address', 0)) if z_emb else None,
                'z_file_addr_hex': None if not z_emb else (
                    hex(z_emb['address'] - td.get('base_offset', 0))
                    if td.get('base_subtract')
                    else hex(z_emb['address'] + td.get('base_offset', 0))
                ),
                'z_rows': z_emb.get('row_count'),
                'z_cols': z_emb.get('col_count'),
                'z_bits': z_emb.get('element_size_bits'),
                'z_type_flags': hex(z_emb.get('type_flags', 0)) if z_emb else None,
                'z_major_stride': z_emb.get('major_stride_bits'),
                'z_minor_stride': z_emb.get('minor_stride_bits'),
                'z_math': z.get('math'),
                'z_units': z.get('units'),
                'x_index_count': x.get('index_count'),
                'x_static_len': len(x.get('static_values') or []),
                'x_emb_rows': x_emb.get('row_count'),
                'x_emb_cols': x_emb.get('col_count'),
                'y_index_count': y.get('index_count'),
                'y_static_len': len(y.get('static_values') or []),
                'y_emb_rows': y_emb.get('row_count'),
                'y_emb_cols': y_emb.get('col_count'),
                'sample_hex': sample_hex,
                'workflow_bucket': metadata['workflow_bucket'],
                'primary_category_path': metadata['primary_category_path'],
                'family_title': metadata['family_title'],
                'variant_label': metadata['variant_label'],
            })

        return jsonify({'count': len(out), 'tables': out})

    except XdfFormatError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 500


@app.route('/api/preferences', methods=['GET'])
def api_preferences_get():
    return jsonify(load_preferences())


@app.route('/api/preferences', methods=['POST'])
def api_preferences_post():
    try:
        payload = request.get_json(silent=True) or {}
        prefs = save_preferences(payload)
        return jsonify(prefs)
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 400


@app.route('/api/project/status', methods=['GET'])
def api_project_status():
    store = PROJECTS.load()
    return jsonify({
        'project': serialize_store_for_api(store),
        'hosting': get_hosting_status(),
    })


@app.route('/api/project/active', methods=['POST'])
def api_project_active():
    try:
        payload = request.get_json(silent=True) or {}
        project = PROJECTS.set_active_project(str(payload.get('project_id') or ''))
        if not project:
            return jsonify({'error': 'Unknown project'}), 404
        return jsonify({'project': serialize_store_for_api(PROJECTS.load())})
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 400


@app.route('/api/project/bins', methods=['GET'])
def api_project_bins():
    project = PROJECTS.get_active_project()
    if not project:
        return jsonify({'error': 'No active project configured in the Windows tray app'}), 404
    return jsonify({'bins': list_project_bins(project), 'project': serialize_store_for_api(PROJECTS.load())})


@app.route('/api/project/bin', methods=['POST'])
def api_project_bin():
    try:
        payload = request.get_json(silent=True) or {}
        project = PROJECTS.get_active_project()
        if not project:
            return jsonify({'error': 'No active project configured in the Windows tray app'}), 404
        bin_path = _resolve_project_bin_payload(project, payload.get('path'))
        updated = PROJECTS.update_active_project({
            'last_bin_path': str(bin_path),
            'bin_folder': project.get('bin_folder') or str(bin_path.parent),
        })
        return jsonify({'project': serialize_store_for_api(PROJECTS.load()), 'active_project': updated})
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 400


@app.route('/api/project/parse', methods=['POST'])
def api_project_parse():
    try:
        payload = request.get_json(silent=True) or {}
        project = PROJECTS.get_active_project()
        if not project:
            return jsonify({'error': 'No active project configured in the Windows tray app'}), 404
        if payload.get('bin_path'):
            bin_path = _resolve_project_bin_payload(project, payload.get('bin_path'))
            project = PROJECTS.update_active_project({
                'last_bin_path': str(bin_path),
                'bin_folder': project.get('bin_folder') or str(bin_path.parent),
            })
        xdf_path, bin_path = resolve_project_paths(project)
        xdf_bytes = xdf_path.read_bytes()
        bin_data = bin_path.read_bytes()
        session = SESSIONS.create(
            xdf_bytes=xdf_bytes,
            bin_bytes=bin_data,
            xdf_name=xdf_path.name,
            bin_name=bin_path.name,
        )
        return jsonify(serialize_session(session))
    except XdfFormatError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 400


@app.route('/api/project/draft/save', methods=['POST'])
def api_project_draft_save():
    try:
        payload = request.get_json(silent=True) or {}
        session = SESSIONS.get(payload.get('session_id', ''))
        project = PROJECTS.get_active_project()
        if not project:
            return jsonify({'error': 'No active project configured in the Windows tray app'}), 404
        draft_path = get_project_draft_path(project)
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(json.dumps(session.to_payload(), indent=2), encoding='utf-8')
        PROJECTS.update_active_project({'draft_path': str(draft_path)})
        return jsonify({
            'draft_path': str(draft_path),
            'session': session.get_summary(),
            'project': serialize_store_for_api(PROJECTS.load()),
        })
    except KeyError as exc:
        return jsonify({'error': str(exc)}), 404
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 400


@app.route('/api/project/draft/restore', methods=['POST'])
def api_project_draft_restore():
    try:
        project = PROJECTS.get_active_project()
        if not project:
            return jsonify({'error': 'No active project configured in the Windows tray app'}), 404
        draft_path = get_project_draft_path(project)
        if not draft_path.exists():
            return jsonify({'error': 'No project draft has been saved yet'}), 404
        session = EditSession.from_payload(json.loads(draft_path.read_text(encoding='utf-8')))
        SESSIONS.store(session)
        return jsonify(serialize_session(session))
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 400


@app.route('/api/project/save-mode', methods=['POST'])
def api_project_save_mode():
    try:
        payload = request.get_json(silent=True) or {}
        mode = payload.get('remote_save_mode')
        if mode not in {'new_revision', 'small_edit'}:
            return jsonify({'error': 'remote_save_mode must be new_revision or small_edit'}), 400
        PROJECTS.update_active_project({'remote_save_mode': mode})
        return jsonify({'project': serialize_store_for_api(PROJECTS.load())})
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 400


@app.route('/api/project/export', methods=['POST'])
def api_project_export():
    try:
        payload = request.get_json(silent=True) or {}
        session = SESSIONS.get(payload.get('session_id', ''))
        project = PROJECTS.get_active_project()
        if not project:
            return jsonify({'error': 'No active project configured in the Windows tray app'}), 404
        output_path, metadata = build_project_export_path(
            project,
            source_bin_name=session.source_bin_name,
            description=payload.get('description') if 'description' in payload else None,
        )
        result = session.export_bin_and_log(output_path, log_dir=project.get('log_folder') or None)
        PROJECTS.update_active_project({
            'last_bin_path': str(output_path),
            'bin_folder': project.get('bin_folder') or str(output_path.parent),
            'output_folder': str(output_path.parent),
            'revision_prefix': metadata['revision_prefix'],
            'revision_number': metadata['next_revision_number'],
            'small_edit_output_path': metadata['small_edit_output_path'],
        })
        if payload.get('download', True) is False:
            return jsonify({
                'filename': Path(result['bin_path']).name,
                'export_path': result['bin_path'],
                'log_path': result['log_path'],
                'remote_save_mode': metadata['mode'],
                'project': serialize_store_for_api(PROJECTS.load()),
                'session': result['session'],
            })
        response = send_file(
            BytesIO(Path(result['bin_path']).read_bytes()),
            mimetype='application/octet-stream',
            as_attachment=True,
            download_name=Path(result['bin_path']).name,
        )
        response.headers['X-Export-Path'] = result['bin_path']
        response.headers['X-Export-Log-Path'] = result['log_path']
        response.headers['X-Remote-Save-Mode'] = metadata['mode']
        return response
    except KeyError as exc:
        return jsonify({'error': str(exc)}), 404
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 400


@app.route('/api/compare', methods=['POST'])
def api_compare():
    try:
        xdf_file = request.files.get('xdf')
        base_file = request.files.get('bin')
        compare_file = request.files.get('compare_bin')

        if not xdf_file:
            return jsonify({'error': 'XDF file is required'}), 400
        if not base_file:
            return jsonify({'error': 'Base BIN file is required'}), 400
        if not compare_file:
            return jsonify({'error': 'Compare BIN file is required'}), 400

        xdf_bytes = xdf_file.read()
        base_bytes = base_file.read()
        compare_bytes = compare_file.read()

        session = SESSIONS.create(
            xdf_bytes=xdf_bytes,
            bin_bytes=base_bytes,
            xdf_name=xdf_file.filename or 'input.xdf',
            bin_name=base_file.filename or 'base.bin',
            bin2_bytes=compare_bytes,
            bin2_name=compare_file.filename or 'compare.bin',
        )
        tables = session.list_tables()
        changed_tables = [table for table in tables if table.get('has_diff') and _table_has_meaningful_diff(table)]

        return jsonify({
            'summary': {
                'xdf_name': session.source_xdf_name,
                'base_bin_name': session.source_bin_name,
                'compare_bin_name': session.reference_bin_name,
                'changed_table_count': len(changed_tables),
                'raw_change_range_count': len(_build_raw_diff_ranges(base_bytes, compare_bytes)),
            },
            'session': session.get_summary(),
            'tables': changed_tables,
            'raw_ranges': _build_raw_diff_ranges(base_bytes, compare_bytes),
            'preferences': load_preferences(),
        })
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 500


@app.route('/api/log/parse', methods=['POST'])
def api_log_parse():
    try:
        session_id = str(request.form.get('session_id') or request.args.get('session_id') or '').strip()
        if not session_id:
            return jsonify({'error': 'session_id is required'}), 400
        session = SESSIONS.get(session_id)
        log_file = request.files.get('log')
        if not log_file:
            return jsonify({'error': 'CSV log file is required'}), 400
        log_data = parse_log_csv(log_file.read(), filename=log_file.filename or 'log.csv')
        SESSION_LOGS[session.id] = log_data
        return jsonify({
            'log': _serialize_log_state(log_data),
            'session': session.get_summary(),
            'preferences': load_preferences(),
        })
    except KeyError as exc:
        return jsonify({'error': str(exc)}), 404
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 400


@app.route('/api/log/overlay', methods=['POST'])
def api_log_overlay():
    try:
        payload = request.get_json(silent=True) or {}
        session_id = str(payload.get('session_id') or '').strip()
        table_id = str(payload.get('table_id') or '').strip()
        if not session_id or not table_id:
            return jsonify({'error': 'session_id and table_id are required'}), 400
        session = SESSIONS.get(session_id)
        log_data = SESSION_LOGS.get(session_id)
        if not log_data:
            return jsonify({'error': 'No CSV log is loaded for this session'}), 404
        table = session.get_table(table_id)
        mapping = payload.get('mapping') or {}
        saved_mapping = ((load_preferences().get('log') or {}).get('axis_mappings') or {}).get(table.get('family_key') or '', {})
        resolved_mapping = infer_axis_mapping(table, log_data, saved_mapping)
        resolved_mapping.update({key: value for key, value in mapping.items() if value is not None})
        overlay = build_log_overlay(
            log_data,
            table,
            resolved_mapping,
            sample_index=payload.get('sample_index', 0),
            start_index=payload.get('range_start'),
            end_index=payload.get('range_end'),
            trace_columns=payload.get('trace_columns') or [],
        )
        return jsonify({
            'overlay': overlay,
            'mapping': resolved_mapping,
            'session': session.get_summary(),
        })
    except KeyError as exc:
        return jsonify({'error': str(exc)}), 404
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 400


@app.route('/api/session/<session_id>', methods=['GET'])
def api_session_summary(session_id):
    try:
        session = SESSIONS.get(session_id)
        return jsonify(serialize_session(session))
    except KeyError as exc:
        return jsonify({'error': str(exc)}), 404


@app.route('/api/session/<session_id>/table/<table_id>', methods=['GET'])
def api_session_table(session_id, table_id):
    try:
        session = SESSIONS.get(session_id)
        return jsonify({'table': session.get_table(table_id), 'session': session.get_summary()})
    except KeyError as exc:
        return jsonify({'error': str(exc)}), 404
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 500


@app.route('/api/session/<session_id>/preview', methods=['POST'])
def api_session_preview(session_id):
    try:
        session = SESSIONS.get(session_id)
        payload = request.get_json(silent=True) or {}
        preview = session.preview_operation(
            payload.get('table_id', ''),
            payload.get('operation', ''),
            payload.get('selection'),
            payload.get('parameters') or {},
        )
        return jsonify({'preview': preview, 'session': session.get_summary()})
    except KeyError as exc:
        return jsonify({'error': str(exc)}), 404
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 400


@app.route('/api/session/<session_id>/apply', methods=['POST'])
def api_session_apply(session_id):
    try:
        session = SESSIONS.get(session_id)
        payload = request.get_json(silent=True) or {}
        result = session.apply_operation(
            payload.get('table_id', ''),
            payload.get('operation', ''),
            payload.get('selection'),
            payload.get('parameters') or {},
        )
        return jsonify(result)
    except KeyError as exc:
        return jsonify({'error': str(exc)}), 404
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 400


@app.route('/api/session/<session_id>/axis', methods=['POST'])
def api_session_axis(session_id):
    try:
        session = SESSIONS.get(session_id)
        payload = request.get_json(silent=True) or {}
        result = session.apply_axis_edit(
            payload.get('table_id', ''),
            payload.get('axis', ''),
            payload.get('values') or [],
        )
        return jsonify(result)
    except KeyError as exc:
        return jsonify({'error': str(exc)}), 404
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 400


@app.route('/api/session/<session_id>/paste', methods=['POST'])
def api_session_paste(session_id):
    try:
        session = SESSIONS.get(session_id)
        payload = request.get_json(silent=True) or {}
        result = session.paste_values(
            payload.get('table_id', ''),
            payload.get('start_row', 0),
            payload.get('start_col', 0),
            payload.get('values') or [],
        )
        return jsonify(result)
    except KeyError as exc:
        return jsonify({'error': str(exc)}), 404
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 400


@app.route('/api/session/<session_id>/import-preview', methods=['POST'])
def api_session_import_preview(session_id):
    try:
        session = SESSIONS.get(session_id)
        payload = request.get_json(silent=True) or {}
        preview = session.preview_import(
            payload.get('table_id', ''),
            payload.get('import_text', ''),
            mode=payload.get('mode', 'values_only'),
        )
        return jsonify({'preview': preview, 'session': session.get_summary()})
    except KeyError as exc:
        return jsonify({'error': str(exc)}), 404
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 400


@app.route('/api/session/<session_id>/import-apply', methods=['POST'])
def api_session_import_apply(session_id):
    try:
        session = SESSIONS.get(session_id)
        payload = request.get_json(silent=True) or {}
        result = session.apply_import(
            payload.get('table_id', ''),
            payload.get('import_text', ''),
            mode=payload.get('mode', 'values_only'),
        )
        return jsonify(result)
    except KeyError as exc:
        return jsonify({'error': str(exc)}), 404
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 400


@app.route('/api/session/<session_id>/transfer-reference', methods=['POST'])
def api_session_transfer_reference(session_id):
    try:
        session = SESSIONS.get(session_id)
        payload = request.get_json(silent=True) or {}
        result = session.transfer_reference_table(
            payload.get('table_id', ''),
            include_axes=bool(payload.get('include_axes', False)),
        )
        return jsonify(result)
    except KeyError as exc:
        return jsonify({'error': str(exc)}), 404
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 400


@app.route('/api/session/<session_id>/undo', methods=['POST'])
def api_session_undo(session_id):
    try:
        session = SESSIONS.get(session_id)
        session.undo_last()
        return jsonify(serialize_session(session))
    except KeyError as exc:
        return jsonify({'error': str(exc)}), 404
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 400


@app.route('/api/session/<session_id>/reset', methods=['POST'])
def api_session_reset(session_id):
    try:
        session = SESSIONS.get(session_id)
        session.reset_draft()
        return jsonify(serialize_session(session))
    except KeyError as exc:
        return jsonify({'error': str(exc)}), 404
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 400


@app.route('/api/session/<session_id>/diff', methods=['GET'])
def api_session_diff(session_id):
    try:
        session = SESSIONS.get(session_id)
        return jsonify(session.diff_summary())
    except KeyError as exc:
        return jsonify({'error': str(exc)}), 404
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 500


@app.route('/api/session/<session_id>/snapshot', methods=['GET'])
def api_session_snapshot(session_id):
    try:
        session = SESSIONS.get(session_id)
        return jsonify({'payload': session.to_payload(), 'session': session.get_summary()})
    except KeyError as exc:
        return jsonify({'error': str(exc)}), 404
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 500


@app.route('/api/session/restore', methods=['POST'])
def api_session_restore():
    try:
        payload = request.get_json(silent=True) or {}
        session_payload = payload.get('payload')
        if not isinstance(session_payload, dict):
            return jsonify({'error': 'A saved draft payload is required'}), 400
        session = EditSession.from_payload(session_payload)
        SESSIONS.store(session)
        return jsonify(serialize_session(session))
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 400


@app.route('/api/session/<session_id>/export', methods=['POST'])
def api_session_export(session_id):
    try:
        session = SESSIONS.get(session_id)
        payload = request.get_json(silent=True) or {}
        filename, archive_bytes = session.export_bundle(stem=payload.get('stem'))
        return send_file(
            BytesIO(archive_bytes),
            mimetype='application/zip',
            as_attachment=True,
            download_name=filename,
        )
    except KeyError as exc:
        return jsonify({'error': str(exc)}), 404
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 400


@app.route('/api/session/<session_id>/export-bin', methods=['POST'])
def api_session_export_bin(session_id):
    try:
        session = SESSIONS.get(session_id)
        payload = request.get_json(silent=True) or {}
        result = session.export_bin_only(stem=payload.get('stem'))
        response = send_file(
            BytesIO(result['bin_bytes']),
            mimetype='application/octet-stream',
            as_attachment=True,
            download_name=result['filename'],
        )
        response.headers['X-Export-Log-Path'] = result['log_path']
        return response
    except KeyError as exc:
        return jsonify({'error': str(exc)}), 404
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 400


def _table_has_meaningful_diff(table: dict) -> bool:
    grid = table.get('grid') or []
    grid2 = table.get('grid2') or []
    for row_index, row in enumerate(grid):
        other_row = grid2[row_index] if row_index < len(grid2) else []
        for col_index, value in enumerate(row):
            if col_index >= len(other_row):
                return True
            if value != other_row[col_index]:
                return True
    return False


def _build_raw_diff_ranges(base_bytes: bytes, compare_bytes: bytes) -> list[dict]:
    ranges = []
    max_len = min(len(base_bytes), len(compare_bytes))
    start = None
    for index in range(max_len):
        if base_bytes[index] != compare_bytes[index]:
            if start is None:
                start = index
            continue
        if start is not None:
            ranges.append(_make_raw_range(base_bytes, compare_bytes, start, index))
            start = None
    if start is not None:
        ranges.append(_make_raw_range(base_bytes, compare_bytes, start, max_len))
    if len(base_bytes) != len(compare_bytes):
        start = max_len
        end = max(len(base_bytes), len(compare_bytes))
        ranges.append({
            'start': start,
            'end': end,
            'length': end - start,
            'before_hex': base_bytes[start:end].hex(),
            'after_hex': compare_bytes[start:end].hex(),
            'context_before_hex': base_bytes[max(0, start - 8):start].hex(),
            'context_after_hex': compare_bytes[start:end + 8].hex(),
            'unmapped': True,
        })
    return ranges[:400]


def _make_raw_range(base_bytes: bytes, compare_bytes: bytes, start: int, end: int) -> dict:
    return {
        'start': start,
        'end': end,
        'length': end - start,
        'before_hex': base_bytes[start:end].hex(),
        'after_hex': compare_bytes[start:end].hex(),
        'context_before_hex': base_bytes[max(0, start - 8):start].hex(),
        'context_after_hex': compare_bytes[end:min(len(compare_bytes), end + 8)].hex(),
    }


def _serialize_log_state(log_data: dict) -> dict:
    return {
        'log_id': log_data.get('log_id'),
        'filename': log_data.get('filename'),
        'metadata': log_data.get('metadata', {}),
        'header': log_data.get('header', []),
        'time_column': log_data.get('time_column'),
        'sample_count': log_data.get('sample_count', 0),
        'time_range': log_data.get('time_range', {}),
        'column_stats': log_data.get('column_stats', {}),
        'initial_traces': log_data.get('initial_traces', []),
    }


def _is_local_request() -> bool:
    remote = (request.remote_addr or '').split('%')[0]
    return remote in {'127.0.0.1', '::1', 'localhost'} or remote.startswith('127.')


def _resolve_project_bin_payload(project: dict, value: str | None) -> Path:
    bin_path = Path(str(value or '')).expanduser()
    if not bin_path.exists() or not bin_path.is_file():
        raise FileNotFoundError('BIN path is missing or invalid')
    bin_folder = project.get('bin_folder')
    if bin_folder:
        root = Path(bin_folder).expanduser().resolve()
        resolved = bin_path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError('BIN path must be inside the active project BIN folder') from exc
    return bin_path


if __name__ == '__main__':
    app.run(debug=True, port=5000)
