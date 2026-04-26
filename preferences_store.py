"""Shared preferences persistence for viewer favorites and folders."""
from __future__ import annotations

from pathlib import Path
import json


PREFERENCES_PATH = Path(r"C:\Users\Michael Krymski\Documents\N55Maps\viewer_preferences.json")

DEFAULT_PREFERENCES = {
    "version": 1,
    "favorites": {"tables": [], "families": []},
    "folders": [],
    "assignments": {},
    "log": {
        "axis_mappings": {},
        "graph": {"enabled_columns": []},
        "playback_speed": 1.0,
    },
}


def load_preferences() -> dict:
    if not PREFERENCES_PATH.exists():
        return json.loads(json.dumps(DEFAULT_PREFERENCES))
    try:
        raw = json.loads(PREFERENCES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return json.loads(json.dumps(DEFAULT_PREFERENCES))

    prefs = json.loads(json.dumps(DEFAULT_PREFERENCES))
    if isinstance(raw, dict):
        prefs["version"] = raw.get("version", 1)
        favorites = raw.get("favorites") or {}
        prefs["favorites"]["tables"] = _coerce_string_list(favorites.get("tables"))
        prefs["favorites"]["families"] = _coerce_string_list(favorites.get("families"))
        prefs["folders"] = _normalize_folders(raw.get("folders"))
        prefs["assignments"] = _normalize_assignments(raw.get("assignments"))
        prefs["log"] = _normalize_log_prefs(raw.get("log"))
    return prefs


def save_preferences(payload: dict) -> dict:
    prefs = {
        "version": 1,
        "favorites": {
            "tables": _coerce_string_list((payload.get("favorites") or {}).get("tables")),
            "families": _coerce_string_list((payload.get("favorites") or {}).get("families")),
        },
        "folders": _normalize_folders(payload.get("folders")),
        "assignments": _normalize_assignments(payload.get("assignments")),
        "log": _normalize_log_prefs(payload.get("log")),
    }
    PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREFERENCES_PATH.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    return prefs


def _coerce_string_list(values) -> list[str]:
    if not isinstance(values, list):
        return []
    out = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in out:
            out.append(item)
    return out


def _normalize_folders(values) -> list[dict]:
    if not isinstance(values, list):
        return []
    out = []
    seen = set()
    for idx, value in enumerate(values):
        if not isinstance(value, dict):
            continue
        folder_id = str(value.get("id") or f"folder-{idx + 1}").strip()
        name = str(value.get("name") or folder_id).strip()
        if not folder_id or folder_id in seen:
            continue
        seen.add(folder_id)
        out.append({"id": folder_id, "name": name})
    return out


def _normalize_assignments(values) -> dict[str, list[str]]:
    if not isinstance(values, dict):
        return {}
    out = {}
    for key, raw_folders in values.items():
        item_key = str(key or "").strip()
        if not item_key:
            continue
        folders = _coerce_string_list(raw_folders)
        if folders:
            out[item_key] = folders
    return out


def _normalize_log_prefs(values) -> dict:
    data = values if isinstance(values, dict) else {}
    axis_mappings = data.get("axis_mappings") if isinstance(data.get("axis_mappings"), dict) else {}
    normalized_mappings = {}
    for key, value in axis_mappings.items():
        family_key = str(key or "").strip()
        if not family_key or not isinstance(value, dict):
            continue
        mapping = {}
        for field in ("x_column", "y_column", "map_column"):
            candidate = str(value.get(field) or "").strip()
            if candidate:
                mapping[field] = candidate
        if mapping:
            normalized_mappings[family_key] = mapping

    graph = data.get("graph") if isinstance(data.get("graph"), dict) else {}
    enabled_columns = _coerce_string_list(graph.get("enabled_columns"))
    playback_speed = graph.get("playback_speed", data.get("playback_speed", 1.0))
    try:
        playback_speed = float(playback_speed)
    except (TypeError, ValueError):
        playback_speed = 1.0
    if playback_speed <= 0:
        playback_speed = 1.0

    return {
        "axis_mappings": normalized_mappings,
        "graph": {
            "enabled_columns": enabled_columns,
        },
        "playback_speed": playback_speed,
    }
