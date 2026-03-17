"""
BIN reader for TunerPro XDF-defined tables.
Reads raw ECU binary data, applies XDF scaling equations, and returns structured tables.
"""
import struct
import math as _math


# ---------------------------------------------------------------------------
# Low-level binary read
# ---------------------------------------------------------------------------

def _read_raw(bin_data: bytes, address: int, size_bits: int, type_flags: int,
              default_le: bool = False, default_signed: bool = False) -> float:
    """
    Read a single numeric value from bin_data at byte address.

    type_flags bit meanings (TunerPro convention):
      bit 1 (0x02) – signed integer
      bit 2 (0x04) – little-endian (LSB first)
      bit 3 (0x08) – IEEE 754 float
    """
    size_bytes = max(size_bits // 8, 1)

    if address < 0 or address + size_bytes > len(bin_data):
        return 0.0

    raw = bin_data[address: address + size_bytes]

    # TunerPro mmedtypeflags bit layout (verified empirically):
    #   bit 0 (0x01) = signed integer
    #   bit 1 (0x02) = little-endian (LSB first)
    #   bit 2 (0x04) = IEEE 754 float
    # When a flag bit is absent, fall back to the XDF DEFAULTS header values.
    is_signed = bool(type_flags & 0x01) or (default_signed and not (type_flags & 0x01))
    is_le     = bool(type_flags & 0x02) or (default_le    and not (type_flags & 0x02))
    is_float  = bool(type_flags & 0x04)
    endian    = '<' if is_le else '>'

    try:
        if is_float:
            if size_bits == 32:
                return float(struct.unpack(endian + 'f', raw)[0])
            elif size_bits == 64:
                return float(struct.unpack(endian + 'd', raw)[0])
            return 0.0
        else:
            fmt_map = {8: ('b','B'), 16: ('h','H'), 32: ('i','I'), 64: ('q','Q')}
            signed_fmt, unsigned_fmt = fmt_map.get(size_bits, ('B','B'))
            fmt = signed_fmt if is_signed else unsigned_fmt
            return float(struct.unpack(endian + fmt, raw)[0])
    except struct.error:
        return 0.0


# ---------------------------------------------------------------------------
# Math equation evaluator
# ---------------------------------------------------------------------------

_SAFE_MATH = {k: getattr(_math, k) for k in dir(_math) if not k.startswith('_')}
_SAFE_MATH.update({'abs': abs, 'int': int, 'float': float, 'round': round,
                   'max': max, 'min': min})


def _apply_math(equation: str, x: float) -> float:
    """Safely evaluate a TunerPro scaling equation with X as the raw value."""
    if not equation or equation.strip() in ('X', 'x'):
        return x
    try:
        env = dict(_SAFE_MATH)
        env['X'] = x
        env['x'] = x
        return float(eval(equation, {"__builtins__": {}}, env))  # noqa: S307
    except Exception:
        return x


# ---------------------------------------------------------------------------
# Axis helpers
# ---------------------------------------------------------------------------

def _file_address(xdf_address: int, base_offset: int, base_subtract: bool) -> int:
    """Convert an XDF logical address to a file byte offset."""
    return (xdf_address - base_offset) if base_subtract else (xdf_address + base_offset)


def _axis_count(axis: dict | None) -> int:
    """
    Return the number of elements in a 1-D axis (X or Y), using the best
    available source: static DATAGRID length > index_count > embedded data size.
    """
    if axis is None:
        return 0
    static = axis.get('static_values')
    if static:
        return len(static)
    ic = axis.get('index_count', 0)
    if ic > 1:
        return ic
    emb = axis.get('embedded') or {}
    return max(emb.get('row_count', 1), emb.get('col_count', 1), 1)


# ---------------------------------------------------------------------------
# Core data reader
# ---------------------------------------------------------------------------

def _read_axis_data(bin_data: bytes, axis: dict | None,
                    base_offset: int, base_subtract: bool,
                    force_rows: int | None = None,
                    force_cols: int | None = None,
                    default_le: bool = False,
                    default_signed: bool = False) -> list:
    """
    Return a flat list of scaled floats for one axis.
    Uses static DATAGRID values when present; otherwise reads from BIN.

    force_rows / force_cols override the row/col count from embeddeddata
    (needed when the Z axis dimensions were inferred from X/Y axis sizes).
    """
    if axis is None:
        return []

    math_eq = axis.get('math') or 'X'

    # Static axis labels baked into the XDF — no BIN read needed
    static = axis.get('static_values')
    if static is not None:
        return [_apply_math(math_eq, v) for v in static]

    embedded = axis.get('embedded')
    if embedded is None:
        return []

    xdf_addr       = embedded['address']
    type_flags     = embedded['type_flags']
    size_bits      = embedded['element_size_bits']
    size_bytes     = max(size_bits // 8, 1)
    rows           = force_rows if force_rows is not None else max(embedded['row_count'], 1)
    cols           = force_cols if force_cols is not None else max(embedded['col_count'], 1)
    major_stride_b = embedded['major_stride_bits']   # bits; negative = column-major
    minor_stride_b = embedded['minor_stride_bits']   # bits; 0 = packed

    file_addr = _file_address(xdf_addr, base_offset, base_subtract)
    col_major = major_stride_b < 0

    # ── Stride calculation ──────────────────────────────────────────────────
    # TunerPro semantics:
    #   major_stride_bits = bits between the START of row N and row N+1
    #                       (or col N and col N+1 when negative / col-major)
    #   minor_stride_bits = bits between consecutive elements within a row/col
    #                       0 means packed (= element_size_bits)
    #
    # When the stored major stride is non-zero but smaller than what a fully
    # packed layout would require, fall back to the packed natural stride.
    # This handles XDFs where the stride field encodes element-level alignment
    # rather than full row/column stride.

    natural_minor = size_bytes                        # bytes per element
    natural_major = (rows * size_bytes) if col_major else (cols * size_bytes)

    if minor_stride_b == 0:
        minor_stride_bytes = natural_minor
    else:
        minor_stride_bytes = max(abs(minor_stride_b) // 8, natural_minor)

    if major_stride_b == 0:
        major_stride_bytes = natural_major
    else:
        explicit_major = abs(major_stride_b) // 8
        # If the explicit value is too small to hold the packed column/row,
        # ignore it and use the natural packed stride instead.
        if explicit_major < natural_major:
            major_stride_bytes = natural_major
        else:
            major_stride_bytes = explicit_major

    values = []
    if col_major:
        # Column-major: iterate columns, then rows within each column
        for c in range(cols):
            for r in range(rows):
                addr = file_addr + c * major_stride_bytes + r * minor_stride_bytes
                values.append(_apply_math(math_eq,
                    _read_raw(bin_data, addr, size_bits, type_flags, default_le, default_signed)))
    else:
        # Row-major: iterate rows, then columns within each row
        for r in range(rows):
            for c in range(cols):
                addr = file_addr + r * major_stride_bytes + c * minor_stride_bytes
                values.append(_apply_math(math_eq,
                    _read_raw(bin_data, addr, size_bits, type_flags, default_le, default_signed)))

    return values


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_table(bin_data: bytes, table_def: dict) -> dict:
    """
    Given raw BIN bytes and one parsed XDF definition (from xdf_parser.parse_xdf),
    return a dict with:
      grid     – 2D list of scaled floats  [row][col]
      x_labels – list of column label values (may be empty)
      y_labels – list of row label values (may be empty)
      rows, cols – dimensions
      units    – unit string from the Z axis
    """
    base_offset    = table_def.get('base_offset', 0)
    base_subtract  = table_def.get('base_subtract', False)
    default_le     = table_def.get('default_lsbfirst', False)
    default_signed = table_def.get('default_signed', False)

    z_axis = table_def.get('z_axis')
    x_axis = table_def.get('x_axis')
    y_axis = table_def.get('y_axis')

    # ── Step 1: determine table dimensions ─────────────────────────────────
    z_emb  = (z_axis or {}).get('embedded') or {}
    z_rows = max(z_emb.get('row_count', 1), 1)
    z_cols = max(z_emb.get('col_count', 1), 1)

    # How many elements does each axis label array have?
    x_count = _axis_count(x_axis) if x_axis else 0   # columns
    y_count = _axis_count(y_axis) if y_axis else 0   # rows

    # Priority:
    #   1. Z-axis embedded row/col (most authoritative when > 1×1)
    #   2. X/Y axis element counts (for XDFs where Z just stores a flat array)
    #   3. Fall back to z_emb values (may be 1×1 for scalars)
    if z_rows > 1 or z_cols > 1:
        rows, cols = z_rows, z_cols
    elif y_count > 1 and x_count > 1:
        rows, cols = y_count, x_count
    elif x_count > 1:
        rows, cols = 1, x_count
    elif y_count > 1:
        rows, cols = y_count, 1
    else:
        rows, cols = z_rows, z_cols

    # ── Step 2: read data ───────────────────────────────────────────────────
    # Pass the inferred dimensions into _read_axis_data so it reads the right
    # number of elements (especially when z_emb says 1×1 but table is bigger).
    kw = dict(base_offset=base_offset, base_subtract=base_subtract,
              default_le=default_le, default_signed=default_signed)

    z_data   = _read_axis_data(bin_data, z_axis, force_rows=rows, force_cols=cols, **kw)
    x_labels = _read_axis_data(bin_data, x_axis, **kw)
    y_labels = _read_axis_data(bin_data, y_axis, **kw)

    # ── Step 3: reshape flat list → 2-D grid ───────────────────────────────
    total = len(z_data)
    if total == 0:
        z_data = [0.0]
        total  = 1

    # If z_emb was flat (e.g. 1×N or N×1) but we inferred 2-D shape, reshape.
    # z_data is already in row-major order from _read_axis_data.
    grid = []
    for r in range(rows):
        row = []
        for c in range(cols):
            idx = r * cols + c
            row.append(z_data[idx] if idx < total else 0.0)
        grid.append(row)

    units = (z_axis or {}).get('units', '') or ''

    return {
        'grid':     grid,
        'x_labels': x_labels,
        'y_labels': y_labels,
        'rows':     rows,
        'cols':     cols,
        'units':    units,
    }
