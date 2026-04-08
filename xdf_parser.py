"""
XDF Parser for TunerPro XDF files.
XDF is XML-based and describes the layout of tables/scalars in a binary ECU file.
"""
import xml.etree.ElementTree as ET


def _parse_int(value, default=0):
    """Parse an integer that may be hex (0x...) or decimal."""
    if value is None:
        return default
    value = value.strip()
    try:
        return int(value, 0)
    except (ValueError, TypeError):
        return default


def _parse_float(value, default=0.0):
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _parse_embedded_data(elem):
    """Parse an <EMBEDDEDDATA> element into a structured dict."""
    if elem is None:
        return None
    return {
        'address':           _parse_int(elem.get('mmedaddress', '0x0')),
        'type_flags':        _parse_int(elem.get('mmedtypeflags', '0x00')),
        'element_size_bits': _parse_int(elem.get('mmedelementsizebits', '8')),
        'row_count':         _parse_int(elem.get('mmedrowcount', '1')),
        'col_count':         _parse_int(elem.get('mmedcolcount', '1')),
        'major_stride_bits': _parse_int(elem.get('mmedmajorstridebits', '0')),
        'minor_stride_bits': _parse_int(elem.get('mmedminorstridebits', '0')),
    }


def _parse_math(axis_elem):
    """Extract scaling equation string from an axis element."""
    math_elem = axis_elem.find('MATH')
    if math_elem is not None:
        eq = math_elem.get('equation', 'X')
        return eq if eq else 'X'
    return 'X'


def _parse_static_values(axis_elem):
    """Extract static DATAGRID values if present (no BIN read needed)."""
    datagrid = axis_elem.find('DATAGRID')
    if datagrid is None:
        return None
    values = []
    for gv in datagrid.findall('GRIDVALUE'):
        values.append(_parse_float(gv.text, 0.0))
    return values if values else None


def _parse_axis(axis_elem):
    """Parse a single <XDFAXIS> element."""
    if axis_elem is None:
        return None
    # TunerPro XDF uses uppercase <EMBEDDEDDATA>; XML is case-sensitive.
    # IMPORTANT: do NOT use `or` — ET elements with no children evaluate as falsy.
    _emb = axis_elem.find('EMBEDDEDDATA')
    if _emb is None:
        _emb = axis_elem.find('embeddeddata')  # fallback for mixed-case XDFs
    embedded = _parse_embedded_data(_emb)
    return {
        'id':            axis_elem.get('id', ''),
        'embedded':      embedded,
        'math':          _parse_math(axis_elem),
        'static_values': _parse_static_values(axis_elem),
        'units':         axis_elem.findtext('units', '').strip(),
        'index_count':   _parse_int(axis_elem.findtext('indexcount', '1')),
        'label':         axis_elem.findtext('label', '').strip(),
        'decimal_places': _parse_int(axis_elem.findtext('decimalpl', '-1')),
    }


def _parse_category_memberships(elem):
    """Return list of category indices this table belongs to (from CATEGORYMEM elements)."""
    indices = []
    for cm in elem.findall('CATEGORYMEM'):
        idx = _parse_int(cm.get('category', '-1'))
        if idx >= 0:
            indices.append(idx)
    return indices


def _parse_table(elem):
    """Parse an <XDFTABLE> element (1D or 2D table)."""
    title = (elem.findtext('title') or 'Unknown Table').strip()
    description = (elem.findtext('description') or '').strip()

    axes = {}
    for ax in elem.findall('XDFAXIS'):
        aid = ax.get('id', '')
        axes[aid] = _parse_axis(ax)

    z_axis = axes.get('z')
    if z_axis is None:
        return None

    return {
        'type':              'table',
        'title':             title,
        'description':       description,
        'unique_id':         elem.get('uniqueid', ''),
        'x_axis':            axes.get('x'),
        'y_axis':            axes.get('y'),
        'z_axis':            z_axis,
        'category_indices':  _parse_category_memberships(elem),
        'is_autogen':        '(autogen)' in title.lower(),
    }


def _parse_constant(elem):
    """Parse an <XDFCONSTANT> element (scalar)."""
    title = (elem.findtext('title') or 'Unknown Constant').strip()
    description = (elem.findtext('description') or '').strip()

    z_axis = None
    for ax in elem.findall('XDFAXIS'):
        if ax.get('id') == 'z':
            z_axis = _parse_axis(ax)
            break

    if z_axis is None:
        return None

    return {
        'type':             'constant',
        'title':            title,
        'description':      description,
        'unique_id':        elem.get('uniqueid', ''),
        'x_axis':           None,
        'y_axis':           None,
        'z_axis':           z_axis,
        'category_indices': _parse_category_memberships(elem),
        'is_autogen':       '(autogen)' in title.lower(),
    }


def parse_xdf(xdf_bytes):
    """
    Parse XDF bytes and return (definitions, categories_map).

    definitions   – list of table/constant dicts
    categories_map – {int_index: str_name} from <CATEGORY> elements in the header
    """
    if isinstance(xdf_bytes, bytes):
        xdf_bytes = xdf_bytes.lstrip(b'\xef\xbb\xbf')
        text = xdf_bytes.decode('utf-8', errors='replace')
    else:
        text = xdf_bytes

    root = ET.fromstring(text)

    # ── Header ────────────────────────────────────────────────────────────
    base_offset      = 0
    base_subtract    = False
    default_lsbfirst = False
    default_signed   = False
    categories_map   = {}   # {index: name}

    header = root.find('XDFHEADER')
    if header is not None:
        bo = header.find('BASEOFFSET')
        if bo is not None:
            base_offset   = _parse_int(bo.get('offset', '0'))
            base_subtract = bo.get('subtract', '0') == '1'

        dflts = header.find('DEFAULTS')
        if dflts is not None:
            default_lsbfirst = dflts.get('lsbfirst', '0') == '1'
            default_signed   = dflts.get('signed',   '0') == '1'

        for cat_elem in header.findall('CATEGORY'):
            idx  = _parse_int(cat_elem.get('index', '0'))
            name = cat_elem.get('name', f'Category {idx}')
            categories_map[idx] = name

    # ── Definitions ───────────────────────────────────────────────────────
    common = {
        'base_offset':      base_offset,
        'base_subtract':    base_subtract,
        'default_lsbfirst': default_lsbfirst,
        'default_signed':   default_signed,
    }

    definitions = []
    for elem in root:
        tag = elem.tag
        if tag == 'XDFTABLE':
            t = _parse_table(elem)
            if t:
                t.update(common)
                definitions.append(t)
        elif tag == 'XDFCONSTANT':
            c = _parse_constant(elem)
            if c:
                c.update(common)
                definitions.append(c)

    return definitions, categories_map
