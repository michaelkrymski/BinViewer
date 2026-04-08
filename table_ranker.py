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
    "Quick Start",
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

QUICK_START_RULES = [
    "wgdc base",
    "wgdc p-factor",
    "wgdc d-factor",
    "commanded wgdc",
    "boost ceiling",
    "fuel (bank 1)",
    "fuel (bank 2)",
    "fuel (main)",
    "fuel (spool)",
    "fuel scalar",
    "max fuel scalar",
    "lambda target min",
    "lambda limit rich",
    "fuel start",
    "timing (main)",
    "timing (cold)",
    "timing (spool)",
    "timing cor. factor",
    "knock reco.",
    "spark dwell",
    "load ceiling",
    "torque request ceiling",
    "modeled torque limit",
    "load to torque limit",
    "load target offset",
    "load limiting factor",
    "boost limit multiplier",
    "torque reduction factor",
    "egt ceiling",
    "clutch",
    "hpfp delivery rate",
    "pvdmx",
    "vanos in",
    "vanos ex",
    "mhd+ custom",
    "antilag",
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
    ("fuel (bank 1)", 5100),
    ("fuel (bank 2)", 5050),
    ("commanded wgdc", 6000),
    ("wgdc base", 5600),
    ("wgdc p-factor", 5400),
    ("wgdc d-factor", 5300),
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

MAP1_FAMILY_RULES = [
    (re.compile(r"^fuel \(bank 1\)$", re.IGNORECASE), "Fuel (Main)", "Map 1"),
    (re.compile(r"^fuel \(bank 2\)$", re.IGNORECASE), "Fuel (Secondary)", "Map 1"),
    (re.compile(r"^timing \(main\)$", re.IGNORECASE), "Timing (Main)", "Map 1"),
    (re.compile(r"^timing \(spool\)$", re.IGNORECASE), "Timing (Spool)", "Map 1"),
]

BUILTIN_DESCRIPTION_OVERLAY = {
    "Fuel (Main)": {
        "short": "Primary AFR target table for the active map.",
        "long": "This is the base fueling target table you shape first for steady high-load fueling. On N55 variants with mirrored banks, keep paired bank tables aligned unless you intentionally need bank-specific behavior.",
        "bucket": "Quick Start",
        "aliases": ["Fuel (Bank 1)", "Fuel (Bank 2)"],
        "priority_note": "Core startup fueling table",
    },
    "Fuel (Spool)": {
        "short": "AFR target table used during spool mode below the spool-mode RPM gate.",
        "long": "This table gives you a separate fueling target during the low-RPM spool phase. It is one of the first places to review when transient spool behavior and turbo response are the focus.",
        "bucket": "Quick Start",
    },
    "Lambda Target Min": {
        "short": "Global richest lambda floor that the DME will not command beyond.",
        "long": "If your richer fuel targets go below this floor, the DME will clamp them here. This is a core guardrail table for ethanol and high-load enrichment work.",
        "bucket": "Quick Start",
    },
    "Lambda Limit Rich": {
        "short": "Rich-side clamp that can override richer fuel targets.",
        "long": "This limiter can win over your main fuel tables if it is set leaner than the commanded target. It is especially important to review when setting ethanol or spool enrichment targets.",
        "bucket": "Quick Start",
    },
    "Fuel Scalar": {
        "short": "Feedforward fueling multiplier used to correct base fueling error.",
        "long": "Use this to move the model closer so trims stay under control before you start relying on correction. It is one of the quickest tables to touch when injector or fuel hardware changes shift the base fueling model.",
        "bucket": "Quick Start",
    },
    "Timing (Main)": {
        "short": "Primary warm ignition timing map for normal high-load operation.",
        "long": "This is the main spark table you usually tune first for power and knock resistance once the engine is warm. Small changes here can have a large impact on torque, MBT approach, and safety margin.",
        "bucket": "Quick Start",
    },
    "Timing (Spool)": {
        "short": "Ignition timing path used during spool mode.",
        "long": "This table shapes timing during the spool-up phase below the spool-mode gate. It is useful when tuning turbo response, transient feel, and knock behavior during early boost build.",
        "bucket": "Quick Start",
    },
    "Load Ceiling": {
        "short": "Primary maximum load limiter that caps requested/allowed load.",
        "long": "This is one of the first load and torque limiters to review when you raise boost or airflow capability. If it stays too low, the rest of the tune can be ready but the ECU still will not allow the requested load.",
        "bucket": "Quick Start",
        "aliases": ["Load ceiling (main)"],
    },
    "Torque Request Ceiling": {
        "short": "Hard torque request cap by RPM or operating mode.",
        "long": "This limiter can cap torque demand before the rest of the torque model has a chance to work. On higher-output setups it often needs to be brought in line with the rest of the torque strategy.",
        "bucket": "Quick Start",
        "aliases": ["Torque request ceiling (Auto)"],
    },
    "Load To Torque Limit": {
        "short": "Conversion limit table that defines how load is translated into modeled torque.",
        "long": "This table helps keep the torque model coherent as you extend the operating range. It is important to preserve sensible low-end behavior while expanding the upper load range for bigger turbo setups.",
        "bucket": "Quick Start",
        "aliases": ["Load to Torque limit 1", "Load to Torque limit 2"],
    },
    "Load Limit Factor": {
        "short": "VE or pressure-to-load factor that strongly affects model accuracy.",
        "long": "This is one of the most important model-shaping tables on a hardware-changed setup because it influences how pressure and airflow become calculated load. It is worth keeping near the top of any quick-start list for larger turbo work.",
        "bucket": "Quick Start",
        "aliases": ["Load limiting factor", "Boost Limit multiplier", "Schluckgerade"],
    },
    "WGDC Base": {
        "short": "Primary base wastegate duty table for boost control.",
        "long": "This is one of the most frequently tuned boost tables because it sets the feedforward wastegate duty target that the rest of the control loop trims around. It is a core starting point for MHD+ custom boost setups.",
        "bucket": "Quick Start",
        "aliases": ["WGDC Base (Custom)", "WGDC Base (Custom) (Antilag)"],
    },
    "WGDC P-Factor": {
        "short": "Proportional correction table in the custom WGDC control strategy.",
        "long": "This table shapes how aggressively boost error becomes a WGDC correction. It works with the base table and D-factor to set the feel and stability of the closed-loop boost response.",
        "bucket": "Quick Start",
        "aliases": ["WGDC P-Factor (Custom)", "WGDC P-Factor (Custom) (Antilag)"],
    },
    "WGDC D-Factor": {
        "short": "Derivative-style damping table for the custom WGDC control strategy.",
        "long": "This table helps calm fast boost error changes and can improve overshoot control. It belongs alongside WGDC Base and WGDC P-Factor when setting up a new custom boost calibration.",
        "bucket": "Quick Start",
        "aliases": ["WGDC D-Factor (Custom)", "WGDC D-Factor (Custom) (Antilag)"],
    },
    "HPFP Delivery Rate": {
        "short": "High-pressure fuel pump delivery model used by the ECU fuel system calculations.",
        "long": "This is especially important on upgraded pump hardware because it affects how the ECU predicts available high-pressure fuel flow. It is a strong candidate for a quick-start review on fuel-system-changed cars.",
        "bucket": "Quick Start",
    },
    "PVDMX": {
        "short": "Key model table to review when extending the setup beyond stock airflow assumptions.",
        "long": "You called this table out specifically for the build, so it is pinned into Quick Start. It should stay easy to find while the exact calibration strategy around it gets refined.",
        "bucket": "Quick Start",
    },
}


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

    family_title = family_title or title
    effective_map_variant = map_variant
    map_inferred = False
    for pattern, inferred_family, inferred_map in MAP1_FAMILY_RULES:
        if pattern.match(family_title):
            family_title = inferred_family
            if effective_map_variant is None:
                effective_map_variant = inferred_map
                map_inferred = True
            break

    return {
        "family_title": family_title,
        "normalized_family_title": normalize_title(family_title),
        "map_variant": map_variant,
        "effective_map_variant": effective_map_variant,
        "path_variant": path_variant,
        "mode_variant": mode_variant,
        "axis_suffix": axis_suffix,
        "variant_label": variant_label,
        "map_inferred": map_inferred,
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
    entries = _normalize_overlay_entries(BUILTIN_DESCRIPTION_OVERLAY)
    if not DESCRIPTION_OVERLAY_PATH.exists():
        return entries

    try:
        raw = json.loads(DESCRIPTION_OVERLAY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return entries

    file_entries = _normalize_overlay_entries(raw)
    entries.update(file_entries)
    return entries


def _normalize_overlay_entries(raw: dict | list) -> dict[str, dict]:
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
        "effective_map_variant": variants["effective_map_variant"],
        "map_inferred": variants["map_inferred"],
        "is_shared_global": _is_shared_global(variants, normalized, table_def),
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
    if any(keyword in normalized or keyword in haystack for keyword in QUICK_START_RULES):
        return "Quick Start"
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
    if workflow_bucket == "Quick Start":
        score += 2500
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


def _is_shared_global(variants: dict, normalized: str, table_def: dict) -> bool:
    if variants.get("map_variant"):
        return False
    if variants.get("map_inferred"):
        return False
    return True
