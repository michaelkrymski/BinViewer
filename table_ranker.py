"""Tuning workflow ranking helpers for XDF table metadata."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from functools import lru_cache
from pathlib import Path
import json
import re


LOG_ROOT = Path(r"C:\Users\Michael Krymski\Documents\N55Maps")
DESCRIPTION_OVERLAY_PATH = LOG_ROOT / "table_descriptions.json"

LOG_LINE_RE = re.compile(
    r"(?P<timestamp>\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})?\s*Table:\s+(?P<title>.*?)\s+changed\.",
    re.IGNORECASE,
)
CATEGORY_SPLIT_RE = re.compile(r"[\\/]+|::|→|\s*>\s*|\s+-\s+")
VARIANT_RE = re.compile(r"\((Map\s*\d+|Path\s*\d+|Mode\s*\d+)\)", re.IGNORECASE)

LOW_PRIORITY_RULES = [
    ("autogen", "Autogen helper table"),
    ("burble", "Burble tuning table"),
    ("exhaust sound", "Exhaust sound table"),
    ("perf display", "Performance display helper"),
    ("display scaling", "Display scaling helper"),
    ("gauge scaling", "Gauge scaling helper"),
]

BUCKET_ORDER = [
    "Boost / WGDC",
    "Fueling / Ethanol",
    "Ignition",
    "Torque / Load / Limits",
    "Map Switching / Multi-map",
    "Rail / HPFP / Fuel System",
    "Diagnostics / Display / Utility",
    "Other",
    "Low Priority",
]

BUCKET_RULES = [
    (
        "Boost / WGDC",
        [
            "commanded wgdc",
            "wgdc",
            "boost ceiling",
            "boost target",
            "boost limit",
            "wastegate",
            "antilag boost",
            "turbo",
        ],
    ),
    (
        "Fueling / Ethanol",
        [
            "fuel scalar",
            "fuel (main)",
            "fuel (spool)",
            "fuel",
            "ethanol",
            "lambda",
            "afr",
            "mixture",
            "inject",
        ],
    ),
    (
        "Ignition",
        [
            "timing (main)",
            "timing (spool)",
            "timing",
            "spark",
            "ignition",
            "knock",
            "dwell",
        ],
    ),
    (
        "Torque / Load / Limits",
        [
            "load ceiling",
            "load limit",
            "load to torque",
            "torque",
            "driver wish",
            "limiter",
            "limit factor",
        ],
    ),
    (
        "Map Switching / Multi-map",
        [
            "active map slots",
            "enable map switch",
            "map switch",
            "map slot",
            "multimap",
            "multi-map",
        ],
    ),
    (
        "Rail / HPFP / Fuel System",
        [
            "rail pressure",
            "hpfp",
            "delivery rate",
            "fuel system",
            "feedforward",
            "msv",
        ],
    ),
    (
        "Diagnostics / Display / Utility",
        [
            "diagnostic",
            "monitor",
            "readiness",
            "display",
            "gauge",
            "utility",
            "power display",
        ],
    ),
]

SEED_PRIORITY_RULES = [
    ("commanded wgdc", 6000),
    ("wgdc base", 5600),
    ("wgdc p-factor", 5400),
    ("boost ceiling (relative) (gear x rpm)", 5200),
    ("boost ceiling", 5000),
    ("fuel scalar", 4800),
    ("fuel (main)", 4700),
    ("fuel (spool)", 4600),
    ("timing (main)", 4500),
    ("timing (spool)", 4400),
    ("static ethanol content", 4300),
    ("load ceiling", 4200),
    ("load limit factor", 4100),
    ("load to torque limit", 4000),
    ("rail pressure target", 3900),
    ("hpfp delivery rate", 3800),
    ("active map slots", 3700),
    ("enable map switch", 3600),
]


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", str(title).strip()).lower()


def split_category_path(name: str) -> list[str]:
    return [part.strip() for part in CATEGORY_SPLIT_RE.split(str(name)) if part.strip()]


def parse_title_variants(title: str) -> dict:
    title = re.sub(r"\s+", " ", str(title).strip())
    map_variant = None
    path_variant = None
    mode_variant = None

    def replace_variant(match: re.Match[str]) -> str:
        nonlocal map_variant, path_variant, mode_variant
        raw = re.sub(r"\s+", " ", match.group(1).strip())
        lower = raw.lower()
        if lower.startswith("map"):
            map_variant = raw.title()
        elif lower.startswith("path"):
            path_variant = raw.title()
        elif lower.startswith("mode"):
            mode_variant = raw.title()
        return ""

    family = VARIANT_RE.sub(replace_variant, title)
    axis_suffix = None
    if " - " in family:
        family, axis_suffix = family.rsplit(" - ", 1)
        axis_suffix = axis_suffix.strip()

    family_title = re.sub(r"\s+", " ", family).strip()
    variant_parts = [part for part in [map_variant, path_variant, mode_variant, axis_suffix] if part]
    variant_label = " / ".join(variant_parts)

    return {
        "family_title": family_title or title,
        "normalized_family_title": normalize_title(family_title or title),
        "map_variant": map_variant,
        "path_variant": path_variant,
        "mode_variant": mode_variant,
        "axis_suffix": axis_suffix,
        "variant_label": variant_label,
    }


@lru_cache(maxsize=1)
def load_tuning_history() -> dict[str, dict]:
    history: dict[str, dict] = defaultdict(lambda: {"change_count": 0, "last_changed_at": None})

    if not LOG_ROOT.exists():
        return {}

    for log_path in LOG_ROOT.rglob("*.log"):
        try:
            text = log_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for line in text.splitlines():
            match = LOG_LINE_RE.search(line)
            if not match:
                continue

            raw_title = match.group("title").strip()
            titles = [normalize_title(raw_title), parse_title_variants(raw_title)["normalized_family_title"]]
            timestamp = _parse_timestamp(match.group("timestamp"))
            for title in titles:
                entry = history[title]
                entry["change_count"] += 1
                if timestamp and (entry["last_changed_at"] is None or timestamp > entry["last_changed_at"]):
                    entry["last_changed_at"] = timestamp

    return dict(history)


@lru_cache(maxsize=1)
def load_description_overlay() -> dict[str, dict]:
    if not DESCRIPTION_OVERLAY_PATH.exists():
        return {}

    try:
        raw = json.loads(DESCRIPTION_OVERLAY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    entries = {}
    if isinstance(raw, dict):
        iterable = raw.items()
        for key, value in iterable:
            data = value if isinstance(value, dict) else {"short": str(value)}
            title = data.get("title") or key
            aliases = data.get("aliases") or []
            records = [title, *aliases, key]
            entry = {
                "title": title,
                "short": str(data.get("short", "")).strip(),
                "long": str(data.get("long", "")).strip(),
                "bucket": str(data.get("bucket", "")).strip() or None,
                "priority_note": str(data.get("priority_note", "")).strip() or None,
            }
            for record in records:
                if record:
                    entries[normalize_title(record)] = entry
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            aliases = item.get("aliases") or []
            entry = {
                "title": title,
                "short": str(item.get("short", "")).strip(),
                "long": str(item.get("long", "")).strip(),
                "bucket": str(item.get("bucket", "")).strip() or None,
                "priority_note": str(item.get("priority_note", "")).strip() or None,
            }
            for record in [title, *aliases]:
                if record:
                    entries[normalize_title(record)] = entry

    return entries


def build_table_metadata(table_def: dict, categories_map: dict[int, str]) -> dict:
    title = table_def.get("title", "")
    description = (table_def.get("description", "") or "").strip()
    variants = parse_title_variants(title)
    normalized = normalize_title(title)
    family_normalized = variants["normalized_family_title"]
    category_names = [categories_map.get(idx, f"Category {idx}") for idx in table_def.get("category_indices", [])]
    primary_category_path = _choose_primary_category_path(category_names)

    history = load_tuning_history()
    history_entry = history.get(normalized) or history.get(family_normalized) or {"change_count": 0, "last_changed_at": None}
    change_count = history_entry["change_count"]
    last_changed_at = history_entry["last_changed_at"]

    overlay = load_description_overlay()
    exact_overlay = overlay.get(normalized)
    family_overlay = overlay.get(family_normalized)
    descriptions = _resolve_descriptions(exact_overlay, family_overlay, description)

    haystack = " ".join([title, description, variants["family_title"], *category_names]).lower()
    low_priority_reason = _detect_low_priority(table_def, haystack)

    bucket_override = (exact_overlay or family_overlay or {}).get("bucket") if (exact_overlay or family_overlay) else None
    workflow_bucket = "Low Priority" if low_priority_reason else (bucket_override or _classify_bucket(normalized, haystack))
    priority_score = _score_table(
        normalized=normalized,
        haystack=haystack,
        workflow_bucket=workflow_bucket,
        change_count=change_count,
        last_changed_at=last_changed_at,
        low_priority_reason=low_priority_reason,
    )

    return {
        "workflow_bucket": workflow_bucket,
        "priority_score": priority_score,
        "change_count": change_count,
        "last_changed_at": last_changed_at.isoformat() if last_changed_at else None,
        "low_priority_reason": low_priority_reason,
        "primary_category_path": primary_category_path or ["Uncategorised"],
        "family_title": variants["family_title"],
        "map_variant": variants["map_variant"],
        "path_variant": variants["path_variant"],
        "mode_variant": variants["mode_variant"],
        "variant_label": variants["variant_label"],
        "description_short": descriptions["description_short"],
        "description_long": descriptions["description_long"],
        "description_source": descriptions["description_source"],
        "family_description_short": descriptions["family_description_short"],
        "family_description_long": descriptions["family_description_long"],
        "family_description_source": descriptions["family_description_source"],
    }


def bucket_sort_key(bucket: str) -> tuple[int, str]:
    try:
        return (BUCKET_ORDER.index(bucket), bucket.lower())
    except ValueError:
        return (len(BUCKET_ORDER), bucket.lower())


def _choose_primary_category_path(category_names: list[str]) -> list[str]:
    paths = [split_category_path(name) for name in category_names if name]
    if not paths:
        return []
    return max(paths, key=lambda path: (len(path), sum(len(part) for part in path)))


def _resolve_descriptions(exact_overlay: dict | None, family_overlay: dict | None, xdf_description: str) -> dict:
    if exact_overlay:
        table_short = exact_overlay.get("short") or xdf_description
        table_long = exact_overlay.get("long") or ""
        table_source = "overlay"
    elif family_overlay:
        table_short = family_overlay.get("short") or xdf_description
        table_long = family_overlay.get("long") or ""
        table_source = "overlay"
    elif xdf_description:
        table_short = xdf_description
        table_long = ""
        table_source = "xdf"
    else:
        table_short = ""
        table_long = ""
        table_source = "none"

    if family_overlay:
        family_short = family_overlay.get("short") or xdf_description
        family_long = family_overlay.get("long") or ""
        family_source = "overlay"
    elif xdf_description:
        family_short = xdf_description
        family_long = ""
        family_source = "xdf"
    else:
        family_short = ""
        family_long = ""
        family_source = "none"

    return {
        "description_short": table_short,
        "description_long": table_long,
        "description_source": table_source,
        "family_description_short": family_short,
        "family_description_long": family_long,
        "family_description_source": family_source,
    }


def _detect_low_priority(table_def: dict, haystack: str) -> str | None:
    if table_def.get("is_autogen"):
        return "Autogen helper table"

    for keyword, reason in LOW_PRIORITY_RULES:
        if keyword in haystack:
            return reason
    return None


def _classify_bucket(normalized: str, haystack: str) -> str:
    for bucket, keywords in BUCKET_RULES:
        if any(keyword in normalized or keyword in haystack for keyword in keywords):
            return bucket
    return "Other"


def _score_table(*, normalized: str, haystack: str, workflow_bucket: str, change_count: int, last_changed_at: datetime | None, low_priority_reason: str | None) -> int:
    score = 0
    if change_count:
        score += 10000 + (change_count * 120)
    if last_changed_at:
        age_days = max((datetime.now() - last_changed_at).days, 0)
        score += max(365 - age_days, 0)
    for phrase, bonus in SEED_PRIORITY_RULES:
        if phrase in normalized:
            score += bonus
    for bucket, keywords in BUCKET_RULES:
        if bucket != workflow_bucket:
            continue
        score += sum(150 for keyword in keywords if keyword in haystack)
        break
    if low_priority_reason:
        score -= 50000
    return score


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%m/%d/%Y %H:%M:%S")
    except ValueError:
        return None
