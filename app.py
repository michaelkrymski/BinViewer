"""
TunerPro XDF/BIN Viewer Flask backend.
"""
import traceback
from flask import Flask, jsonify, render_template, request

from bin_reader import extract_table
from table_ranker import build_table_metadata, load_description_overlay, load_tuning_history
from xdf_parser import parse_xdf

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/parse', methods=['POST'])
def api_parse():
    try:
        xdf_file = request.files.get('xdf')
        bin_file = request.files.get('bin')
        bin2_file = request.files.get('bin2')

        if not xdf_file:
            return jsonify({'error': 'XDF file is required'}), 400
        if not bin_file:
            return jsonify({'error': 'BIN file is required'}), 400

        xdf_bytes = xdf_file.read()
        bin_data = bin_file.read()
        bin2_data = bin2_file.read() if bin2_file else None

        table_defs, categories_map = parse_xdf(xdf_bytes)
        categories = {str(k): v for k, v in categories_map.items()}

        load_tuning_history()
        load_description_overlay()

        results = []
        for td in table_defs:
            metadata = build_table_metadata(td, categories_map)
            try:
                data = extract_table(bin_data, td)
                entry = {
                    'title': td['title'],
                    'description': td.get('description', ''),
                    'type': td['type'],
                    'units': data['units'],
                    'grid': data['grid'],
                    'x_labels': data['x_labels'],
                    'y_labels': data['y_labels'],
                    'rows': data['rows'],
                    'cols': data['cols'],
                    'has_diff': False,
                    'grid2': None,
                    'error': None,
                    'category_indices': td.get('category_indices', []),
                    'is_autogen': td.get('is_autogen', False),
                    **metadata,
                }

                if bin2_data is not None:
                    data2 = extract_table(bin2_data, td)
                    entry['grid2'] = data2['grid']
                    entry['has_diff'] = True

                results.append(entry)
            except Exception as exc:
                results.append({
                    'title': td.get('title', 'Unknown'),
                    'description': td.get('description', ''),
                    'type': td.get('type', 'unknown'),
                    'units': '',
                    'grid': [],
                    'x_labels': [],
                    'y_labels': [],
                    'rows': 0,
                    'cols': 0,
                    'has_diff': False,
                    'grid2': None,
                    'error': str(exc),
                    'category_indices': td.get('category_indices', []),
                    'is_autogen': td.get('is_autogen', False),
                    **metadata,
                })

        return jsonify({'tables': results, 'count': len(results), 'categories': categories})

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

    except Exception as exc:
        return jsonify({'error': str(exc), 'traceback': traceback.format_exc()}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000)
