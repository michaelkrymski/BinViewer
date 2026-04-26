"""CSV log parsing and table overlay helpers."""
from __future__ import annotations

from io import StringIO
from pathlib import Path
import csv
import math
import re


DEFAULT_TRACE_CANDIDATES = [
    "RPM (rpm)",
    "Load actual RAM",
    "Throttle Position (*)",
    "Throttle Pos. Req (*)",
    "WGDC (%)",
    "Boost (PSI)",
    "Boost target RAM (PSI)",
    "Timing Cyl. 1",
    "Current Map (-)",
]

RPM_COLUMN_CANDIDATES = ["rpm (rpm)", "rpm"]
LOAD_COLUMN_CANDIDATES = ["load actual ram", "load req. (%)", "load req.", "load"]
GEAR_COLUMN_CANDIDATES = ["gear (-)", "gear"]
MAP_COLUMN_CANDIDATES = ["current map (-)", "current map", "map"]
ETHANOL_COLUMN_CANDIDATES = ["fuel interpolation (-)", "fuel scalar interpolation (-)", "ethanol", "ethanol content"]


def parse_log_csv(csv_bytes: bytes, *, filename: str = "log.csv") -> dict:
    text = csv_bytes.decode("utf-8-sig", errors="ignore")
    metadata = {}
    data_lines = []
    header = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            payload = line[1:].strip()
            if ":" in payload:
                key, value = payload.split(":", 1)
                metadata[key.strip()] = value.strip()
            continue
        if header is None and line.lower().startswith("time,"):
            header = next(csv.reader([line]))
            continue
        if header is not None:
            data_lines.append(raw_line)

    if not header:
        raise ValueError("Could not find a CSV header row beginning with 'Time,'.")

    reader = csv.DictReader(StringIO("\n".join(data_lines)), fieldnames=header)
    columns = [str(name or "").strip() for name in header]
    numeric_columns = {column: [] for column in columns}
    samples: list[dict] = []
    for row_index, row in enumerate(reader):
        sample = {"_row_index": row_index}
        for column in columns:
            raw_value = (row.get(column) or "").strip()
            sample[column] = _parse_numeric(raw_value)
            if isinstance(sample[column], (int, float)) and math.isfinite(float(sample[column])):
                numeric_columns[column].append(float(sample[column]))
        samples.append(sample)

    if not samples:
        raise ValueError("The CSV log contains no data rows.")

    time_column = _find_matching_column(columns, ["time"])
    if not time_column:
        raise ValueError("The CSV log is missing a usable Time column.")

    times = [float(sample.get(time_column) or 0.0) for sample in samples]
    initial_traces = [column for column in DEFAULT_TRACE_CANDIDATES if column in columns]
    if not initial_traces:
        initial_traces = [column for column in columns if column != time_column][:6]

    return {
        "log_id": f"log-{Path(filename).stem}-{len(samples)}",
        "filename": filename,
        "metadata": metadata,
        "header": columns,
        "time_column": time_column,
        "samples": samples,
        "sample_count": len(samples),
        "time_range": {"start": times[0], "end": times[-1]},
        "column_stats": {
            column: {
                "min": min(values) if values else None,
                "max": max(values) if values else None,
                "count": len(values),
            }
            for column, values in numeric_columns.items()
        },
        "initial_traces": initial_traces,
    }


def build_log_overlay(
    log_data: dict,
    table: dict,
    mapping: dict,
    *,
    sample_index: int = 0,
    start_index: int | None = None,
    end_index: int | None = None,
    trace_columns: list[str] | None = None,
) -> dict:
    rows = int(table.get("rows") or 0)
    cols = int(table.get("cols") or 0)
    if rows <= 0 or cols <= 0:
        raise ValueError("Selected table does not expose a usable grid for log overlay.")

    samples = log_data["samples"]
    sample_count = len(samples)
    current_index = min(max(int(sample_index or 0), 0), max(sample_count - 1, 0))
    range_start = min(max(int(start_index or 0), 0), max(sample_count - 1, 0))
    range_end = min(max(int(end_index if end_index is not None else sample_count - 1), range_start), max(sample_count - 1, 0))

    x_column = str(mapping.get("x_column") or "").strip()
    y_column = str(mapping.get("y_column") or "").strip()
    map_column = str(mapping.get("map_column") or "").strip()
    x_labels = list(table.get("x_labels") or [])
    y_labels = list(table.get("y_labels") or [])
    if len(x_labels) != cols or len(y_labels) != rows:
        raise ValueError("Table axis labels do not match the table dimensions.")
    needs_x = cols > 1
    needs_y = rows > 1
    if needs_x and not x_column:
        raise ValueError("An X log column is required for this table overlay.")
    if needs_y and not y_column:
        raise ValueError("A Y log column is required for this table overlay.")

    target_map = _extract_map_number(table.get("effective_map_variant"))
    time_column = log_data["time_column"]
    dwell = [[0.0 for _ in range(cols)] for _ in range(rows)]
    hit_count = [[0 for _ in range(cols)] for _ in range(rows)]
    mapped_samples = 0
    unmapped_samples = 0
    active_cell = None
    current_values = {}
    current_sample = samples[current_index]

    if x_column in current_sample:
        current_values["x_value"] = current_sample.get(x_column)
    if y_column in current_sample:
        current_values["y_value"] = current_sample.get(y_column)
    if map_column:
        current_values["map_value"] = current_sample.get(map_column)
    current_values["time"] = current_sample.get(time_column)

    for index in range(range_start, range_end + 1):
        sample = samples[index]
        if not _sample_matches_map(sample, map_column, target_map):
            unmapped_samples += 1
            continue

        resolved = resolve_sample_to_cell(sample, x_column, y_column, x_labels, y_labels, needs_x=needs_x, needs_y=needs_y)
        if resolved is None:
            unmapped_samples += 1
            continue

        row_index, col_index, x_value, y_value = resolved
        mapped_samples += 1
        hit_count[row_index][col_index] += 1
        dwell[row_index][col_index] += _sample_dwell_delta(samples, index, range_end, time_column)

        if index == current_index:
            active_cell = {
                "row": row_index,
                "col": col_index,
                "x_value": x_value,
                "y_value": y_value,
            }

    graph_columns = _sanitize_trace_columns(trace_columns or [], log_data["header"])
    graph_data = build_graph_series(log_data, graph_columns, range_start=range_start, range_end=range_end, map_column=map_column, target_map=target_map)

    max_dwell = max((max(row) for row in dwell), default=0.0)
    return {
        "table_id": table.get("table_id"),
        "sample_index": current_index,
        "range_start": range_start,
        "range_end": range_end,
        "current_sample_time": current_values.get("time"),
        "active_cell": active_cell,
        "dwell_matrix": dwell,
        "hit_count_matrix": hit_count,
        "mapped_sample_count": mapped_samples,
        "unmapped_sample_count": unmapped_samples,
        "current_values": current_values,
        "summary": {
            "max_dwell": max_dwell,
            "visited_cell_count": sum(1 for row in hit_count for value in row if value > 0),
        },
        "graph": graph_data,
    }


def build_graph_series(
    log_data: dict,
    columns: list[str],
    *,
    range_start: int,
    range_end: int,
    map_column: str = "",
    target_map: int | None = None,
) -> dict:
    samples = log_data["samples"]
    time_column = log_data["time_column"]
    indices = []
    times = []
    for index in range(range_start, range_end + 1):
        sample = samples[index]
        if not _sample_matches_map(sample, map_column, target_map):
            continue
        time_value = sample.get(time_column)
        if not _is_finite_number(time_value):
            continue
        indices.append(index)
        times.append(float(time_value))

    stride = max(1, math.ceil(len(indices) / 800))
    sliced_indices = indices[::stride]
    sliced_times = times[::stride]
    series = []
    for column in columns:
        points = []
        for source_index, time_value in zip(sliced_indices, sliced_times):
            value = samples[source_index].get(column)
            if not _is_finite_number(value):
                points.append(None)
            else:
                points.append(float(value))
        values = [point for point in points if point is not None]
        series.append({
            "column": column,
            "values": points,
            "min": min(values) if values else None,
            "max": max(values) if values else None,
        })

    return {
        "time_column": time_column,
        "times": sliced_times,
        "sample_indices": sliced_indices,
        "series": series,
    }


def infer_axis_mapping(table: dict, log_data: dict, saved_mapping: dict | None = None) -> dict:
    saved_mapping = saved_mapping or {}
    columns = list(log_data.get("header") or [])
    if saved_mapping.get("x_column") in columns and saved_mapping.get("y_column") in columns:
        return {
            "x_column": saved_mapping.get("x_column"),
            "y_column": saved_mapping.get("y_column"),
            "map_column": saved_mapping.get("map_column") if saved_mapping.get("map_column") in columns else _find_matching_column(columns, MAP_COLUMN_CANDIDATES),
            "confidence": "saved",
        }

    title = str(table.get("title") or "").lower()
    family = str(table.get("family_title") or "").lower()
    bucket = str(table.get("workflow_bucket") or "").lower()
    x_stats = _axis_label_stats(table.get("x_labels") or [])
    y_stats = _axis_label_stats(table.get("y_labels") or [])
    x_column = None
    y_column = None

    if _looks_like_gear_axis(x_stats) and _looks_like_rpm_axis(y_stats):
        x_column = _find_matching_column(columns, GEAR_COLUMN_CANDIDATES)
        y_column = _find_matching_column(columns, RPM_COLUMN_CANDIDATES)
    elif _looks_like_rpm_axis(x_stats) and _looks_like_load_axis(y_stats):
        x_column = _find_matching_column(columns, RPM_COLUMN_CANDIDATES)
        y_column = _find_matching_column(columns, LOAD_COLUMN_CANDIDATES)
    elif _looks_like_rpm_axis(y_stats) and _looks_like_load_axis(x_stats):
        x_column = _find_matching_column(columns, LOAD_COLUMN_CANDIDATES)
        y_column = _find_matching_column(columns, RPM_COLUMN_CANDIDATES)
    elif "ethanol" in title or "interpolation" in title:
        x_column = _find_matching_column(columns, ETHANOL_COLUMN_CANDIDATES)
    else:
        x_column = _find_matching_column(columns, RPM_COLUMN_CANDIDATES)

    if y_column is None:
        if any(keyword in title or keyword in family for keyword in ("load", "fuel", "timing", "wgdc", "boost", "torque")) or "fueling" in bucket or "ignition" in bucket or "boost" in bucket:
            y_column = _find_matching_column(columns, LOAD_COLUMN_CANDIDATES)
        elif "gear" in title:
            y_column = _find_matching_column(columns, RPM_COLUMN_CANDIDATES)

    map_column = _find_matching_column(columns, MAP_COLUMN_CANDIDATES)
    confidence = "high" if x_column and y_column else "low"
    return {
        "x_column": x_column or "",
        "y_column": y_column or "",
        "map_column": map_column or "",
        "confidence": confidence,
    }


def resolve_sample_to_cell(
    sample: dict,
    x_column: str,
    y_column: str,
    x_labels: list[float],
    y_labels: list[float],
    *,
    needs_x: bool = True,
    needs_y: bool = True,
) -> tuple[int, int, float | None, float | None] | None:
    x_value = sample.get(x_column) if x_column else None
    y_value = sample.get(y_column) if y_column else None
    if needs_x and not _is_finite_number(x_value):
        return None
    if needs_y and not _is_finite_number(y_value):
        return None
    x_index = _nearest_label_index(float(x_value), x_labels) if needs_x else 0
    y_index = _nearest_label_index(float(y_value), y_labels) if needs_y else 0
    if x_index is None or y_index is None:
        return None
    return (y_index, x_index, float(x_value) if needs_x else None, float(y_value) if needs_y else None)


def _nearest_label_index(value: float, labels: list[float]) -> int | None:
    numeric_labels = []
    for index, label in enumerate(labels):
        if _is_finite_number(label):
            numeric_labels.append((index, float(label)))
    if not numeric_labels:
        return None
    return min(numeric_labels, key=lambda item: abs(item[1] - value))[0]


def _sample_dwell_delta(samples: list[dict], index: int, range_end: int, time_column: str) -> float:
    if index >= range_end:
        return 0.0
    current_time = samples[index].get(time_column)
    next_time = samples[index + 1].get(time_column)
    if not _is_finite_number(current_time) or not _is_finite_number(next_time):
        return 0.0
    return max(0.0, float(next_time) - float(current_time))


def _sanitize_trace_columns(columns: list[str], available_columns: list[str]) -> list[str]:
    if not columns:
        return []
    available = set(available_columns)
    return [column for column in columns if column in available]


def _sample_matches_map(sample: dict, map_column: str, target_map: int | None) -> bool:
    if not map_column or target_map is None:
        return True
    value = sample.get(map_column)
    if not _is_finite_number(value):
        return False
    return int(round(float(value))) == target_map


def _extract_map_number(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"(\d+)", str(value))
    return int(match.group(1)) if match else None


def _find_matching_column(columns: list[str], candidates: list[str]) -> str | None:
    normalized = {normalize_column_name(column): column for column in columns}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    for candidate in candidates:
        for normalized_name, original in normalized.items():
            if candidate in normalized_name:
                return original
    return None


def _axis_label_stats(labels: list[float]) -> dict:
    values = [float(label) for label in labels if _is_finite_number(label)]
    if not values:
        return {"min": None, "max": None}
    return {"min": min(values), "max": max(values)}


def _looks_like_rpm_axis(stats: dict) -> bool:
    return stats.get("max") is not None and float(stats["max"]) >= 400


def _looks_like_load_axis(stats: dict) -> bool:
    maximum = stats.get("max")
    return maximum is not None and 5 <= float(maximum) <= 350


def _looks_like_gear_axis(stats: dict) -> bool:
    maximum = stats.get("max")
    return maximum is not None and float(maximum) <= 12


def normalize_column_name(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _parse_numeric(value: str):
    if value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return value


def _is_finite_number(value) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
