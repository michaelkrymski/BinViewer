"""Project profile storage and export naming for the Windows launcher flow."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
import json
import os
import re
import uuid


APP_DIR = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")) / "BinViewer"
PROJECTS_PATH = APP_DIR / "projects.json"
DEFAULT_SAVE_TEMPLATE = "{source_stem}{token}({description})"
REMOTE_SAVE_MODES = {"new_revision", "small_edit"}
BIN_EXTENSIONS = {".bin", ".hex"}


DEFAULT_STORE = {
    "version": 1,
    "active_project_id": None,
    "projects": [],
}


DEFAULT_PROJECT = {
    "id": "",
    "name": "Default Project",
    "xdf_path": "",
    "bin_folder": "",
    "last_bin_path": "",
    "output_folder": "",
    "log_folder": "",
    "draft_path": "",
    "save_template": DEFAULT_SAVE_TEMPLATE,
    "description": "",
    "revision_prefix": "v",
    "revision_number": 1,
    "remote_save_mode": "new_revision",
    "small_edit_output_path": "",
}


class ProjectStore:
    """Small JSON-backed store for named Windows launcher projects."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else PROJECTS_PATH

    def load(self) -> dict:
        if not self.path.exists():
            return deepcopy(DEFAULT_STORE)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return deepcopy(DEFAULT_STORE)
        return normalize_store(raw)

    def save(self, store: dict) -> dict:
        normalized = normalize_store(store)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
        return normalized

    def list_projects(self) -> list[dict]:
        return self.load()["projects"]

    def get_active_project(self) -> dict | None:
        store = self.load()
        return get_active_project(store)

    def upsert_project(self, project: dict, *, make_active: bool = True) -> dict:
        store = self.load()
        normalized = normalize_project(project)
        if not normalized["id"]:
            normalized["id"] = uuid.uuid4().hex
        projects = []
        replaced = False
        for existing in store["projects"]:
            if existing["id"] == normalized["id"]:
                projects.append(normalized)
                replaced = True
            else:
                projects.append(existing)
        if not replaced:
            projects.append(normalized)
        store["projects"] = projects
        if make_active:
            store["active_project_id"] = normalized["id"]
        self.save(store)
        return normalized

    def set_active_project(self, project_id: str) -> dict | None:
        store = self.load()
        if not any(project["id"] == project_id for project in store["projects"]):
            return None
        store["active_project_id"] = project_id
        self.save(store)
        return get_active_project(store)

    def update_active_project(self, updates: dict) -> dict:
        store = self.load()
        active = get_active_project(store)
        if not active:
            active = normalize_project({"name": "Default Project"})
            active["id"] = uuid.uuid4().hex
            store["projects"].append(active)
            store["active_project_id"] = active["id"]

        merged = normalize_project({**active, **updates, "id": active["id"]})
        store["projects"] = [merged if project["id"] == active["id"] else project for project in store["projects"]]
        self.save(store)
        return merged


def normalize_store(raw: dict) -> dict:
    store = deepcopy(DEFAULT_STORE)
    if not isinstance(raw, dict):
        return store
    projects = [normalize_project(project) for project in raw.get("projects", []) if isinstance(project, dict)]
    projects = [project for project in projects if project["id"]]
    store["projects"] = projects
    active_id = str(raw.get("active_project_id") or "").strip()
    store["active_project_id"] = active_id if any(project["id"] == active_id for project in projects) else (
        projects[0]["id"] if projects else None
    )
    return store


def normalize_project(raw: dict) -> dict:
    project = deepcopy(DEFAULT_PROJECT)
    if isinstance(raw, dict):
        for key in project:
            if key in raw:
                project[key] = raw[key]
    project["id"] = str(project.get("id") or "").strip()
    project["name"] = str(project.get("name") or "Default Project").strip() or "Default Project"
    for key in ("xdf_path", "bin_folder", "last_bin_path", "output_folder", "log_folder", "draft_path", "small_edit_output_path"):
        project[key] = str(project.get(key) or "").strip()
    project["save_template"] = str(project.get("save_template") or DEFAULT_SAVE_TEMPLATE).strip() or DEFAULT_SAVE_TEMPLATE
    project["description"] = sanitize_description(project.get("description") or "")
    project["revision_prefix"] = str(project.get("revision_prefix") or "v").strip() or "v"
    try:
        project["revision_number"] = max(1, int(project.get("revision_number") or 1))
    except (TypeError, ValueError):
        project["revision_number"] = 1
    if project.get("remote_save_mode") not in REMOTE_SAVE_MODES:
        project["remote_save_mode"] = "new_revision"
    return project


def get_active_project(store: dict) -> dict | None:
    active_id = store.get("active_project_id")
    for project in store.get("projects", []):
        if project.get("id") == active_id:
            return project
    return None


def serialize_store_for_api(store: dict) -> dict:
    return {
        "version": 1,
        "active_project_id": store.get("active_project_id"),
        "projects": [serialize_project_for_api(project) for project in store.get("projects", [])],
        "active_project": serialize_project_for_api(get_active_project(store)),
    }


def serialize_project_for_api(project: dict | None) -> dict | None:
    if not project:
        return None
    out = deepcopy(project)
    out["xdf_name"] = Path(out["xdf_path"]).name if out.get("xdf_path") else ""
    out["last_bin_name"] = Path(out["last_bin_path"]).name if out.get("last_bin_path") else ""
    out["output_folder_name"] = Path(out["output_folder"]).name if out.get("output_folder") else ""
    out["log_folder_name"] = Path(out["log_folder"]).name if out.get("log_folder") else ""
    return out


def list_project_bins(project: dict, *, limit: int = 200) -> list[dict]:
    if not project.get("bin_folder"):
        return []
    folder = Path(project.get("bin_folder") or "")
    if not folder.exists() or not folder.is_dir():
        return []
    files = [path for path in folder.iterdir() if path.is_file() and (path.suffix.lower() in BIN_EXTENSIONS or not path.suffix)]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return [
        {
            "name": path.name,
            "path": str(path),
            "size": path.stat().st_size,
            "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            "is_last": str(path) == project.get("last_bin_path"),
        }
        for path in files[:limit]
    ]


def resolve_project_paths(project: dict) -> tuple[Path, Path]:
    xdf_path = Path(project.get("xdf_path") or "")
    bin_path = Path(project.get("last_bin_path") or "")
    if not xdf_path.exists() or not xdf_path.is_file():
        raise FileNotFoundError("Active project XDF path is missing or invalid")
    if not bin_path.exists() or not bin_path.is_file():
        raise FileNotFoundError("Active project BIN path is missing or invalid")
    return xdf_path, bin_path


def get_project_draft_path(project: dict) -> Path:
    if project.get("draft_path"):
        return Path(project["draft_path"])
    if project.get("output_folder"):
        return Path(project["output_folder"]) / f"{sanitize_filename_stem(project['name'])}.draft.json"
    return APP_DIR / "drafts" / f"{sanitize_filename_stem(project['name'])}.draft.json"


def build_project_export_path(
    project: dict,
    *,
    source_bin_name: str | None = None,
    description: str | None = None,
) -> tuple[Path, dict]:
    mode = project.get("remote_save_mode") if project.get("remote_save_mode") in REMOTE_SAVE_MODES else "new_revision"
    output_folder = Path(project.get("output_folder") or Path(project.get("last_bin_path") or ".").parent)
    output_folder.mkdir(parents=True, exist_ok=True)
    source_path = Path(project.get("last_bin_path") or source_bin_name or "edited-bin.bin")
    source_stem = sanitize_filename_stem(source_path.stem or Path(source_bin_name or "edited-bin").stem)
    split = split_revision_token(source_stem)
    base_stem = split["base_stem"] or source_stem
    prefix = split["prefix"] or project.get("revision_prefix") or "v"
    has_source_revision = bool(split["number"])
    current_number = split["number"] or max(0, int(project.get("revision_number") or 1) - 1)
    export_description = sanitize_description(
        description if description is not None else (
            project.get("description") or split.get("description") or ""
        )
    )

    if mode == "new_revision":
        next_number = current_number + 1 if has_source_revision else int(project.get("revision_number") or 1)
        token = f"{prefix}{next_number}"
        stem = render_save_template(project.get("save_template") or DEFAULT_SAVE_TEMPLATE, {
            "stem": source_stem,
            "source_stem": base_stem,
            "token": token,
            "n": str(next_number),
            "description": export_description,
            "date": datetime.now().strftime("%Y%m%d"),
            "time": datetime.now().strftime("%H%M%S"),
        })
        advanced_revision_number = next_number + 1
        path = uniquify_path(output_folder / f"{sanitize_filename_stem(stem)}.bin")
        small_edit_output_path = str(path)
    else:
        next_number = max(current_number, int(project.get("revision_number") or 1))
        token = f"{prefix}{current_number}" if current_number else f"{prefix}{next_number}"
        if project.get("small_edit_output_path"):
            path = Path(project["small_edit_output_path"])
            stem = path.stem
        else:
            stem = source_stem
            path = output_folder / f"{sanitize_filename_stem(stem)}.bin"
        advanced_revision_number = int(project.get("revision_number") or 1)
        small_edit_output_path = str(path)

    metadata = {
        "mode": mode,
        "filename": path.name,
        "output_path": str(path),
        "revision_prefix": prefix,
        "revision_number": next_number,
        "next_revision_number": advanced_revision_number,
        "token": token,
        "description": export_description,
        "small_edit_output_path": small_edit_output_path,
    }
    return path, metadata


def render_save_template(template: str, values: dict[str, str]) -> str:
    out = str(template or DEFAULT_SAVE_TEMPLATE)
    for key, value in values.items():
        out = out.replace("{" + key + "}", str(value))
    return out


def split_revision_token(stem: str) -> dict:
    match = re.match(
        r"^(?P<base>.*?)(?:[_\-\s]?)(?P<prefix>v|mk)(?P<number>\d+)(?:\((?P<description>[^)]*)\))?(?P<trailing>.*)$",
        stem,
        re.IGNORECASE,
    )
    if not match:
        return {"base_stem": stem, "prefix": "", "number": 0, "description": ""}
    base = match.group("base").rstrip("_- ")
    prefix = match.group("prefix")
    return {
        "base_stem": base or stem,
        "prefix": prefix,
        "number": int(match.group("number")),
        "description": sanitize_description(match.group("description") or ""),
    }


def uniquify_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not find an available filename near {path}")


def sanitize_filename_stem(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._() -]+", "-", str(value or "").strip()).strip("-")
    return cleaned or "edited-bin"


def sanitize_description(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._() -]+", "-", str(value or "").strip()).strip("-")
