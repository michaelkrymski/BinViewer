"""
TunerPro XDF/BIN Viewer Flask backend.
"""
from io import BytesIO
import traceback
from flask import Flask, jsonify, render_template, request, send_file

from bin_reader import extract_table
from editor_core import EditSession, SessionRegistry
from preferences_store import load_preferences, save_preferences
from table_ranker import build_table_metadata
from xdf_parser import XdfFormatError, parse_xdf

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024
SESSIONS = SessionRegistry()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/compare')
def compare():
    return render_template('compare.html')


def serialize_session(session):
    return {
        'tables': session.list_tables(),
        'count': len(session.bindings),
        'categories': {str(k): v for k, v in session.categories_map.items()},
        'session': session.get_summary(),
        'preferences': load_preferences(),
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
        for td in table_defs:
            metadata = build_table_metadata(td, categories_map)
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

        session = EditSession(
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
            'tables': changed_tables,
            'raw_ranges': _build_raw_diff_ranges(base_bytes, compare_bytes),
            'preferences': load_preferences(),
        })
    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 500


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


if __name__ == '__main__':
    app.run(debug=True, port=5000)
