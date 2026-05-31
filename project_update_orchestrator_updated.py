#!/usr/bin/env python3
"""
Project Update Orchestrator

A GUI-first orchestration tool for large project automation.

Features included in this script:
- Initializes a _PROJECT_UPDATES workspace under a chosen project root
- Maintains structured request queue in requests.json
- Generates human-readable _Project_Plans_.txt from requests.json
- Maintains append-only change_log.json and generated change_log.txt
- Generates versioned project dumps from the project tree
- Builds and saves prompt packages for selected requests
- Optionally submits prompts to the OpenAI Responses API
- Saves raw responses, parses structured patch bundles, validates them,
  applies safe rewrites, creates backups, and runs verification commands
- Launches a Tkinter GUI by default; CLI subcommands remain available

This is designed as an MVP orchestrator with a practical, monolithic deployment.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime
import difflib
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from typing import Any

# ======================================================================================
# Constants and defaults
# ======================================================================================

UPDATES_DIR_NAME = "_PROJECT_UPDATES"
REQUESTS_FILE = "requests.json"
MANIFEST_FILE = "update_manifest.json"
CONFIG_FILE = "config.json"
PROJECT_PLANS_FILE = "_Project_Plans_.txt"
CHANGE_LOG_JSON_FILE = "change_log.json"
CHANGE_LOG_TXT_FILE = "change_log.txt"

DEFAULT_TEXT_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cxx", ".c++",
    ".h", ".hh", ".hpp", ".hxx", ".h++",
    ".ipp", ".inl", ".ixx", ".tpp",
    ".cmake", ".mak", ".make", ".mk",
    ".vcxproj", ".vcproj", ".sln", ".props", ".targets", ".filters",
    ".pro", ".pri",
    ".bat", ".cmd", ".ps1", ".sh", ".py",
    ".txt", ".md", ".rst", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".xml", ".xsd", ".csv", ".tsv",
}

DEFAULT_TEXT_FILENAMES = {
    "CMakeLists.txt", "Makefile", "makefile", "GNUmakefile", "README", "LICENSE",
    ".gitignore", ".gitattributes", ".clang-format", ".clang-tidy", ".editorconfig",
}

DEFAULT_EXCLUDED_DIRS = {
    ".git", ".svn", ".hg", ".vs", ".idea", ".vscode", "__pycache__",
    "build", "builds", "cmake-build-debug", "cmake-build-release", "out", "dist",
    "bin", "obj", "Debug", "Release", "x64", "x86", ".cache", ".mypy_cache", ".pytest_cache",
    UPDATES_DIR_NAME,
}

DEFAULT_BINARY_EXTENSIONS = {
    ".exe", ".dll", ".lib", ".a", ".so", ".dylib",
    ".obj", ".o", ".pdb", ".ilk", ".idb",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
    ".pdf", ".zip", ".7z", ".rar", ".tar", ".gz", ".mp3", ".wav", ".mp4", ".avi", ".mov",
    ".db", ".sqlite", ".sqlite3", ".class", ".jar",
}

REQUEST_STATUSES = [
    "queued", "prepared", "submitted", "response_received", "parsed", "validated", "applied", "verified",
    "failed_prepare", "failed_submit", "failed_parse", "failed_validate", "failed_apply", "failed_verify",
    "rejected", "archived",
]

REQUEST_TYPES = ["feature", "bugfix", "refactor", "ui", "build", "test", "documentation", "architecture"]
REQUEST_PRIORITIES = ["low", "medium", "high", "critical"]

SEP = "=" * 100
CONTENT_SEP = "-" * 100
DUMP_HEADER = "PROJECT ROUNDTRIP EXPORT"

# ======================================================================================
# Utility helpers
# ======================================================================================

def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def log_print(prefix: str, msg: str) -> None:
    print(f"[{prefix}] {msg}")


def info(msg: str) -> None:
    log_print("INFO", msg)


def ok(msg: str) -> None:
    log_print("OK", msg)


def warn(msg: str) -> None:
    log_print("WARN", msg)


def fail(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)


def safe_rel_path(rel_path: str) -> str:
    normalized = rel_path.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def json_dump(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def json_load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def is_hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts if part not in (".", ".."))


def detect_newline_style_from_bytes(data: bytes) -> str:
    if not data:
        return "none"
    if b"\r\n" in data:
        return "crlf"
    if b"\n" in data:
        return "lf"
    if b"\r" in data:
        return "cr"
    return "none"


def newline_style_to_text(style: str | None) -> str:
    style = (style or "lf").lower()
    if style == "crlf":
        return "\r\n"
    if style == "cr":
        return "\r"
    return "\n"


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}


def validate_path_inside_root(project_root: Path, rel_path: str) -> Path:
    rel = safe_rel_path(rel_path)
    p = Path(rel)
    if p.is_absolute():
        raise ValueError(f"Absolute path rejected: {rel_path}")
    if any(part == ".." for part in p.parts):
        raise ValueError(f"Path traversal rejected: {rel_path}")
    target = (project_root.resolve() / p).resolve()
    try:
        target.relative_to(project_root.resolve())
    except ValueError as exc:
        raise ValueError(f"Resolved path escapes project root: {rel_path}") from exc
    return target



def get_configured_api_key(config: dict[str, Any]) -> str:
    api_key = str(config.get("api", {}).get("api_key", "") or "").strip()
    if not api_key:
        raise RuntimeError("No API key is saved in config. Paste the API key into the GUI API Key field and click Save Config.")
    return api_key


# ======================================================================================
# Dataclasses
# ======================================================================================

@dataclass
class FileEntry:
    rel_path: str
    content: str
    sha256: str | None = None
    encoding: str | None = None
    newline: str | None = None
    final_newline: bool = False

# ======================================================================================
# Workspace manager
# ======================================================================================

class Workspace:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.updates_root = self.project_root / UPDATES_DIR_NAME
        self.exports_dir = self.updates_root / "exports"
        self.prompts_dir = self.updates_root / "prompts"
        self.responses_dir = self.updates_root / "responses"
        self.incoming_dir = self.updates_root / "incoming"
        self.backups_dir = self.updates_root / "backups"
        self.reports_dir = self.updates_root / "reports"
        self.logs_dir = self.updates_root / "logs"
        self.requests_path = self.updates_root / REQUESTS_FILE
        self.manifest_path = self.updates_root / MANIFEST_FILE
        self.config_path = self.updates_root / CONFIG_FILE
        self.project_plans_path = self.updates_root / PROJECT_PLANS_FILE
        self.change_log_json_path = self.updates_root / CHANGE_LOG_JSON_FILE
        self.change_log_txt_path = self.updates_root / CHANGE_LOG_TXT_FILE

    def initialize(self) -> None:
        ensure_dir(self.updates_root)
        ensure_dir(self.exports_dir)
        ensure_dir(self.prompts_dir)
        ensure_dir(self.responses_dir)
        ensure_dir(self.incoming_dir)
        ensure_dir(self.backups_dir)
        ensure_dir(self.reports_dir)
        ensure_dir(self.logs_dir)

        if not self.requests_path.exists():
            json_dump(self.requests_path, {"queue_order": [], "requests": {}})
        if not self.manifest_path.exists():
            json_dump(self.manifest_path, {
                "project": {
                    "project_root": str(self.project_root),
                    "project_name": self.project_root.name,
                    "initialized_at": now_iso(),
                },
                "workspace": {
                    "updates_root": str(self.updates_root),
                    "exports_dir": "exports",
                    "prompts_dir": "prompts",
                    "responses_dir": "responses",
                    "incoming_dir": "incoming",
                    "backups_dir": "backups",
                    "reports_dir": "reports",
                    "logs_dir": "logs",
                },
                "versions": {
                    "current_dump_version": 0,
                    "current_request_id": 0,
                    "latest_dump_file": None,
                    "latest_successful_request": None,
                },
                "requests": {},
                "history": [],
            })
        if not self.config_path.exists():
            json_dump(self.config_path, {
                "project_root": str(self.project_root),
                "updates_root": str(self.updates_root),
                "model": {
                    "provider": "openai",
                    "model_name": "gpt-5.4-mini",
                    "response_mode": "structured_patch_bundle",
                },
                "api": {
                    "api_key": "",
                },
                "build": {
                    "build_cmd": "",
                    "test_cmd": "",
                    "run_cmd": "",
                },
                "policies": {
                    "allow_add": True,
                    "allow_delete": False,
                    "max_changed_files_warning_threshold": 25,
                    "require_backup_before_apply": True,
                },
                "ui": {
                    "fullscreen_on_launch": True,
                },
            })
        if not self.change_log_json_path.exists():
            json_dump(self.change_log_json_path, {"events": []})
        if not self.project_plans_path.exists():
            self.project_plans_path.write_text(
                "PROJECT PLANS\nGenerated from requests.json\n",
                encoding="utf-8"
            )
        if not self.change_log_txt_path.exists():
            self.change_log_txt_path.write_text("", encoding="utf-8")

# ======================================================================================
# Request store and export
# ======================================================================================

class RequestsStore:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def load(self) -> dict[str, Any]:
        return json_load(self.workspace.requests_path, {"queue_order": [], "requests": {}})

    def save(self, data: dict[str, Any]) -> None:
        json_dump(self.workspace.requests_path, data)

    def next_request_id(self) -> str:
        manifest = json_load(self.workspace.manifest_path, {})
        current = int(manifest.get("versions", {}).get("current_request_id", 0)) + 1
        manifest["versions"]["current_request_id"] = current
        json_dump(self.workspace.manifest_path, manifest)
        return f"{current:04d}"

    def create_request(self) -> dict[str, Any]:
        data = self.load()
        request_id = self.next_request_id()
        req = {
            "id": request_id,
            "title": f"New Request {request_id}",
            "status": "queued",
            "priority": "medium",
            "type": "feature",
            "created": now_iso(),
            "updated": now_iso(),
            "base_dump_version": None,
            "user_goal": "",
            "technical_constraints": "",
            "files_or_areas": "",
            "acceptance_criteria": "",
            "implementation_notes": "",
            "model_prompt_append": "Return a structured patch bundle.",
        }
        data["requests"][request_id] = req
        data["queue_order"].append(request_id)
        self.save(data)
        return req

    def duplicate_request(self, request_id: str) -> dict[str, Any]:
        data = self.load()
        src = data["requests"][request_id]
        new_id = self.next_request_id()
        req = dict(src)
        req["id"] = new_id
        req["title"] = f"{src['title']} (Copy)"
        req["status"] = "queued"
        req["created"] = now_iso()
        req["updated"] = now_iso()
        req["base_dump_version"] = None
        data["requests"][new_id] = req
        idx = data["queue_order"].index(request_id)
        data["queue_order"].insert(idx + 1, new_id)
        self.save(data)
        return req

    def delete_request(self, request_id: str) -> None:
        data = self.load()
        data["requests"].pop(request_id, None)
        data["queue_order"] = [rid for rid in data["queue_order"] if rid != request_id]
        self.save(data)

    def reorder(self, request_id: str, direction: int) -> None:
        data = self.load()
        order = data["queue_order"]
        if request_id not in order:
            return
        idx = order.index(request_id)
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(order):
            return
        order[idx], order[new_idx] = order[new_idx], order[idx]
        self.save(data)

    def update_request(self, request_id: str, fields: dict[str, Any]) -> None:
        data = self.load()
        req = data["requests"][request_id]
        req.update(fields)
        req["updated"] = now_iso()
        self.save(data)

    def first_queued(self) -> str | None:
        data = self.load()
        for rid in data["queue_order"]:
            if data["requests"][rid]["status"] == "queued":
                return rid
        return None


def export_project_plans(workspace: Workspace) -> None:
    data = json_load(workspace.requests_path, {"queue_order": [], "requests": {}})
    lines = [SEP, "PROJECT PLANS", SEP, "Generated from requests.json", ""]
    for rid in data["queue_order"]:
        req = data["requests"][rid]
        lines.extend([
            SEP,
            f"CHANGE REQUEST {rid}",
            SEP,
            f"TITLE: {req.get('title', '')}",
            f"STATUS: {req.get('status', '')}",
            f"PRIORITY: {req.get('priority', '')}",
            f"TYPE: {req.get('type', '')}",
            f"CREATED: {req.get('created', '')}",
            f"UPDATED: {req.get('updated', '')}",
            f"BASE_DUMP_VERSION: {req.get('base_dump_version', None)}",
            "",
            "USER_GOAL:",
            req.get('user_goal', ''),
            "",
            "TECHNICAL_CONSTRAINTS:",
            req.get('technical_constraints', ''),
            "",
            "FILES_OR_AREAS:",
            req.get('files_or_areas', ''),
            "",
            "ACCEPTANCE_CRITERIA:",
            req.get('acceptance_criteria', ''),
            "",
            "IMPLEMENTATION_NOTES:",
            req.get('implementation_notes', ''),
            "",
            "MODEL_PROMPT_APPEND:",
            req.get('model_prompt_append', ''),
            "",
            f"END CHANGE REQUEST {rid}",
            SEP,
            "",
        ])
    workspace.project_plans_path.write_text("\n".join(lines), encoding="utf-8")

# ======================================================================================
# Change log
# ======================================================================================

class ChangeLog:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def append(self, request_id: str | None, event: str, status: str, message: str, artifacts: dict[str, Any] | None = None) -> None:
        data = json_load(self.workspace.change_log_json_path, {"events": []})
        entry = {
            "timestamp": now_iso(),
            "request_id": request_id,
            "event": event,
            "status": status,
            "message": message,
            "artifacts": artifacts or {},
        }
        data["events"].append(entry)
        json_dump(self.workspace.change_log_json_path, data)
        self._export_txt(data)

    def _export_txt(self, data: dict[str, Any]) -> None:
        lines = []
        for e in data.get("events", []):
            lines.append(
                f"{e['timestamp']} | {e.get('request_id') or '-'} | {e['event']} | {e['status']} | {e['message']}"
            )
        self.workspace.change_log_txt_path.write_text("\n".join(lines), encoding="utf-8")

# ======================================================================================
# Manifest manager
# ======================================================================================

class Manifest:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def load(self) -> dict[str, Any]:
        return json_load(self.workspace.manifest_path, {})

    def save(self, data: dict[str, Any]) -> None:
        json_dump(self.workspace.manifest_path, data)

    def append_history(self, request_id: str | None, from_status: str | None, to_status: str, note: str, artifacts: dict[str, Any] | None = None) -> None:
        data = self.load()
        data.setdefault("history", []).append({
            "timestamp": now_iso(),
            "request_id": request_id,
            "from_status": from_status,
            "to_status": to_status,
            "note": note,
            "artifacts": artifacts or {},
        })
        self.save(data)

    def set_request_state(self, request_id: str, request_record: dict[str, Any]) -> None:
        data = self.load()
        data.setdefault("requests", {})[request_id] = request_record
        self.save(data)

    def next_dump_version(self) -> int:
        data = self.load()
        current = int(data.get("versions", {}).get("current_dump_version", 0)) + 1
        data["versions"]["current_dump_version"] = current
        self.save(data)
        return current

    def set_latest_dump(self, version: int, rel_path: str) -> None:
        data = self.load()
        data["versions"]["current_dump_version"] = version
        data["versions"]["latest_dump_file"] = rel_path
        self.save(data)

    def set_latest_successful_request(self, request_id: str) -> None:
        data = self.load()
        data["versions"]["latest_successful_request"] = request_id
        self.save(data)

# ======================================================================================
# Project dump export engine
# ======================================================================================

def should_include_file(path: Path) -> bool:
    if path.name in DEFAULT_TEXT_FILENAMES:
        return True
    if path.suffix.lower() in DEFAULT_TEXT_EXTENSIONS:
        return True
    return looks_like_text_file(path)


def looks_like_text_file(path: Path, sample_size: int = 8192) -> bool:
    if path.suffix.lower() in DEFAULT_BINARY_EXTENSIONS:
        return False
    try:
        sample = path.read_bytes()[:sample_size]
    except OSError:
        return False
    if b"\x00" in sample:
        return False
    if not sample:
        return True
    for enc in ("utf-8", "latin-1"):
        try:
            sample.decode(enc)
            return True
        except UnicodeDecodeError:
            continue
    return False


def read_text_file(path: Path) -> FileEntry:
    raw = path.read_bytes()
    decoded: str | None = None
    encoding_used: str | None = None
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            decoded = raw.decode(enc)
            encoding_used = enc
            break
        except Exception:
            continue
    if decoded is None:
        decoded = raw.decode("utf-8", errors="replace")
        encoding_used = "utf-8"
    return FileEntry(
        rel_path="",
        content=normalize_newlines(decoded),
        sha256=sha256_bytes(raw),
        encoding=encoding_used,
        newline=detect_newline_style_from_bytes(raw),
        final_newline=raw.endswith((b"\n", b"\r")),
    )


def export_project_dump(workspace: Workspace, include_hidden: bool = False, max_file_size_mb: float = 2.0) -> Path:
    manifest = Manifest(workspace)
    version = manifest.next_dump_version()
    output_path = workspace.exports_dir / f"project_dump_v{version:04d}.txt"
    max_bytes = int(max_file_size_mb * 1024 * 1024)

    included: list[Path] = []
    skipped: list[str] = []
    project_root = workspace.project_root

    for root, dirs, files in os.walk(project_root):
        root_path = Path(root)
        pruned = []
        for d in dirs:
            d_path = root_path / d
            if d in DEFAULT_EXCLUDED_DIRS:
                skipped.append(f"SKIP DIR (excluded): {d_path}")
                continue
            if not include_hidden and is_hidden(d_path.relative_to(project_root)):
                skipped.append(f"SKIP DIR (hidden): {d_path}")
                continue
            pruned.append(d)
        dirs[:] = pruned

        for filename in files:
            file_path = root_path / filename
            try:
                rel = file_path.relative_to(project_root)
            except ValueError:
                rel = file_path
            if not include_hidden and is_hidden(rel):
                skipped.append(f"SKIP FILE (hidden): {rel}")
                continue
            if not file_path.is_file():
                skipped.append(f"SKIP FILE (not regular): {rel}")
                continue
            try:
                size = file_path.stat().st_size
            except OSError:
                skipped.append(f"SKIP FILE (stat failed): {rel}")
                continue
            if size > max_bytes:
                skipped.append(f"SKIP FILE (too large): {rel}")
                continue
            if not should_include_file(file_path):
                skipped.append(f"SKIP FILE (not text/code): {rel}")
                continue
            included.append(file_path)

    included.sort(key=lambda p: str(p.relative_to(project_root)).lower())

    with output_path.open("w", encoding="utf-8", newline="\n") as out:
        out.write(SEP + "\n")
        out.write(DUMP_HEADER + "\n")
        out.write(SEP + "\n")
        out.write(f"Project root: {project_root}\n")
        out.write(f"Dump version: v{version:04d}\n")
        out.write(f"Total included files: {len(included)}\n")
        out.write(f"Total skipped items: {len(skipped)}\n\n")
        out.write("INCLUDED FILES\n")
        out.write(CONTENT_SEP + "\n")
        for p in included:
            out.write(f"{p.relative_to(project_root).as_posix()}\n")
        out.write("\nFILE CONTENTS\n")
        out.write(SEP + "\n\n")
        for p in included:
            rel = p.relative_to(project_root).as_posix()
            entry = read_text_file(p)
            out.write(SEP + "\n")
            out.write(f"BEGIN FILE: {rel}\n")
            out.write(f"SHA256: {entry.sha256 or ''}\n")
            out.write(f"ENCODING: {entry.encoding or ''}\n")
            out.write(f"NEWLINE: {entry.newline or 'none'}\n")
            out.write(f"FINAL_NEWLINE: {'true' if entry.final_newline else 'false'}\n")
            out.write(CONTENT_SEP + "\n")
            out.write(entry.content)
            if entry.content and not entry.content.endswith("\n"):
                out.write("\n")
            out.write(CONTENT_SEP + "\n")
            out.write(f"END FILE: {rel}\n")
            out.write(SEP + "\n\n")

    manifest.set_latest_dump(version, str(output_path.relative_to(workspace.updates_root)))
    return output_path

# ======================================================================================
# Prompt builder and response parsing
# ======================================================================================

def build_prompt_text(request: dict[str, Any], dump_text: str, base_dump_version: int) -> str:
    return f"""You are implementing a change request against an existing software project.

Return ONLY a JSON object matching this schema:
{{
  "request_id": "{request['id']}",
  "base_dump_version": {base_dump_version},
  "changes": [
    {{
      "path": "relative/path/to/file",
      "action": "replace|add|delete",
      "content": "full file content for replace/add"
    }}
  ],
  "notes": ["short summary note"]
}}

Rules:
- Only modify files necessary to implement the request.
- Preserve unrelated files.
- Do not use absolute paths.
- Do not use .. path traversal.
- For delete, omit content.
- For replace and add, include full file content.
- Output pure JSON only, no markdown fences.

CHANGE REQUEST
ID: {request['id']}
TITLE: {request['title']}
TYPE: {request['type']}
PRIORITY: {request['priority']}

USER_GOAL:
{request['user_goal']}

TECHNICAL_CONSTRAINTS:
{request['technical_constraints']}

FILES_OR_AREAS:
{request['files_or_areas']}

ACCEPTANCE_CRITERIA:
{request['acceptance_criteria']}

IMPLEMENTATION_NOTES:
{request['implementation_notes']}

MODEL_PROMPT_APPEND:
{request['model_prompt_append']}

PROJECT DUMP VERSION: {base_dump_version}

PROJECT DUMP CONTENT
{dump_text}
"""


def save_prompt_package(workspace: Workspace, request: dict[str, Any], dump_path: Path, dump_version: int) -> Path:
    prompt_text = build_prompt_text(request, dump_path.read_text(encoding="utf-8"), dump_version)
    prompt_path = workspace.prompts_dir / f"request_{request['id']}_prompt_v{dump_version:04d}.txt"
    prompt_path.write_text(prompt_text, encoding="utf-8")
    return prompt_path


def extract_response_text(response_json: dict[str, Any]) -> str:
    if isinstance(response_json.get("output_text"), str):
        return response_json["output_text"]

    collected: list[str] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            if obj.get("type") == "output_text" and isinstance(obj.get("text"), str):
                collected.append(obj["text"])
            if obj.get("type") == "text" and isinstance(obj.get("text"), str):
                collected.append(obj["text"])
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(response_json)
    return "\n".join(s for s in collected if s.strip())


def submit_prompt_to_openai(prompt_text: str, model_name: str, api_key: str) -> dict[str, Any]:
    payload = {
        "model": model_name,
        "input": prompt_text,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8"))


def save_raw_response(workspace: Workspace, request_id: str, dump_version: int, response_payload: dict[str, Any]) -> Path:
    path = workspace.responses_dir / f"request_{request_id}_raw_response_v{dump_version:04d}.json"
    json_dump(path, response_payload)
    return path


def extract_json_object_from_text(text: str) -> dict[str, Any]:
    text = text.strip()
    # direct JSON
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # fenced JSON
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if m:
        return json.loads(m.group(1))

    # first balanced object heuristic
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in response text.")
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start=start):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    candidate = text[start:i+1]
                    return json.loads(candidate)
    raise ValueError("Unable to extract balanced JSON object from response text.")


def normalize_patch_bundle(obj: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "request_id": str(obj.get("request_id", "")).strip(),
        "base_dump_version": int(obj.get("base_dump_version", 0)),
        "changes": [],
        "notes": obj.get("notes", []) if isinstance(obj.get("notes", []), list) else [],
    }
    changes = obj.get("changes", [])
    if not isinstance(changes, list):
        raise ValueError("changes must be a list")
    for c in changes:
        if not isinstance(c, dict):
            raise ValueError("Each change must be an object")
        action = str(c.get("action", "")).strip().lower()
        path = str(c.get("path", "")).strip()
        entry = {"path": path, "action": action}
        if action in {"replace", "add"}:
            entry["content"] = c.get("content", "")
        normalized["changes"].append(entry)
    return normalized

# ======================================================================================
# Validation and apply
# ======================================================================================

def validate_patch_bundle(bundle: dict[str, Any], project_root: Path, request_id: str, base_dump_version: int,
                          allow_delete: bool = False) -> list[str]:
    errors: list[str] = []
    if bundle.get("request_id") != request_id:
        errors.append(f"Request ID mismatch: expected {request_id}, got {bundle.get('request_id')}")
    if int(bundle.get("base_dump_version", 0)) != int(base_dump_version):
        errors.append(
            f"Base dump version mismatch: expected {base_dump_version}, got {bundle.get('base_dump_version')}"
        )
    seen = set()
    for idx, change in enumerate(bundle.get("changes", []), start=1):
        path = str(change.get("path", "")).strip()
        action = str(change.get("action", "")).strip()
        if not path:
            errors.append(f"Change {idx}: empty path")
            continue
        if path in seen:
            errors.append(f"Duplicate path in patch bundle: {path}")
        seen.add(path)
        try:
            validate_path_inside_root(project_root, path)
        except Exception as exc:
            errors.append(str(exc))
        if action not in {"replace", "add", "delete"}:
            errors.append(f"Unsupported action for {path}: {action}")
        if action in {"replace", "add"} and "content" not in change:
            errors.append(f"Missing content for {action}: {path}")
        if action == "delete" and not allow_delete:
            errors.append(f"Delete action not allowed by policy: {path}")
    return errors


def apply_patch_bundle(workspace: Workspace, bundle: dict[str, Any], dry_run: bool, allow_add: bool, allow_delete: bool) -> tuple[Path, list[str]]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = ensure_dir(workspace.backups_dir / timestamp)
    diff_lines: list[str] = []

    for change in bundle.get("changes", []):
        rel_path = change["path"]
        action = change["action"]
        target = validate_path_inside_root(workspace.project_root, rel_path)

        old_text = target.read_text(encoding="utf-8", errors="replace") if target.exists() and target.is_file() else ""

        if action == "delete":
            if not allow_delete:
                raise ValueError(f"Delete not allowed: {rel_path}")
            if dry_run:
                diff_lines.append(f"[DRY-RUN] DELETE {rel_path}")
                continue
            if target.exists():
                backup_path = backup_root / rel_path
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup_path)
                target.unlink()
            diff_lines.append(f"DELETE {rel_path}")
            continue

        if action == "add" and target.exists() and not allow_add:
            raise ValueError(f"Add would overwrite existing file but allow_add is false: {rel_path}")

        new_text = normalize_newlines(str(change.get("content", "")))
        udiff = difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
            n=3,
        )
        diff_lines.append("".join(udiff) or f"[NO TEXT DIFF] {rel_path}")

        if dry_run:
            continue

        if target.exists():
            backup_path = backup_root / rel_path
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new_text, encoding="utf-8", newline="\n")

    diff_report = workspace.reports_dir / f"apply_diff_report_{timestamp}.txt"
    diff_report.write_text("\n\n".join(diff_lines), encoding="utf-8")
    return diff_report, diff_lines

# ======================================================================================
# Verification
# ======================================================================================

def run_command(command: str, cwd: Path) -> tuple[int, str]:
    result = subprocess.run(command, cwd=str(cwd), shell=True, capture_output=True, text=True)
    combined = (result.stdout or "") + ("\n" if result.stdout and result.stderr else "") + (result.stderr or "")
    return int(result.returncode), combined


def run_verification(workspace: Workspace, build_cmd: str, test_cmd: str, run_cmd: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = workspace.reports_dir / f"verify_report_{timestamp}.txt"
    sections = []
    for label, command in [("BUILD", build_cmd), ("TEST", test_cmd), ("RUN", run_cmd)]:
        if not command.strip():
            continue
        rc, output = run_command(command, workspace.project_root)
        sections.append(f"{SEP}\n{label}: {command}\nEXIT CODE: {rc}\n{SEP}\n{output}\n")
        if rc != 0:
            break
    report_path.write_text("\n".join(sections), encoding="utf-8")
    return report_path

# ======================================================================================
# Orchestrator operations
# ======================================================================================

def request_to_manifest_record(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": request.get("title"),
        "status": request.get("status"),
        "type": request.get("type"),
        "priority": request.get("priority"),
        "created": request.get("created"),
        "base_dump_version": request.get("base_dump_version"),
        "prompt_file": request.get("prompt_file"),
        "raw_response_file": request.get("raw_response_file"),
        "incoming_artifact": request.get("incoming_artifact"),
        "apply_report": request.get("apply_report"),
        "verify_report": request.get("verify_report"),
        "notes": request.get("notes", []),
    }


def prepare_request(workspace: Workspace, request_id: str) -> tuple[dict[str, Any], Path, int, Path]:
    store = RequestsStore(workspace)
    clog = ChangeLog(workspace)
    manifest = Manifest(workspace)
    export_project_plans(workspace)

    data = store.load()
    request = data["requests"][request_id]
    old_status = request["status"]
    request["status"] = "prepared"
    store.update_request(request_id, request)
    clog.append(request_id, "prepared", "success", "Request prepared for export")
    manifest.append_history(request_id, old_status, "prepared", "Request prepared")

    dump_path = export_project_dump(workspace)
    dump_version = int(json_load(workspace.manifest_path, {}).get("versions", {}).get("current_dump_version", 0))
    request["base_dump_version"] = dump_version
    prompt_path = save_prompt_package(workspace, request, dump_path, dump_version)
    request["prompt_file"] = str(prompt_path.relative_to(workspace.updates_root))
    store.update_request(request_id, request)
    manifest.set_request_state(request_id, request_to_manifest_record(request))
    export_project_plans(workspace)
    return request, dump_path, dump_version, prompt_path


def submit_request_via_api(workspace: Workspace, request_id: str) -> tuple[Path, dict[str, Any], str]:
    store = RequestsStore(workspace)
    manifest = Manifest(workspace)
    clog = ChangeLog(workspace)
    config = json_load(workspace.config_path, {})
    data = store.load()
    request = data["requests"][request_id]

    prompt_rel = request.get("prompt_file")
    if not prompt_rel:
        raise RuntimeError("Request has no prompt file. Prepare/export first.")
    prompt_path = workspace.updates_root / prompt_rel
    prompt_text = prompt_path.read_text(encoding="utf-8")

    api_key = get_configured_api_key(config)
    model_name = config.get("model", {}).get("model_name", "gpt-5.4-mini")

    response_payload = submit_prompt_to_openai(prompt_text, model_name, api_key)
    raw_path = save_raw_response(workspace, request_id, int(request["base_dump_version"]), response_payload)
    response_text = extract_response_text(response_payload)

    old_status = request["status"]
    request["status"] = "response_received"
    request["raw_response_file"] = str(raw_path.relative_to(workspace.updates_root))
    store.update_request(request_id, request)
    manifest.set_request_state(request_id, request_to_manifest_record(request))
    manifest.append_history(request_id, old_status, "response_received", "Raw response saved", {
        "raw_response_file": request["raw_response_file"],
    })
    clog.append(request_id, "response_received", "success", "Raw response saved", {
        "raw_response_file": request["raw_response_file"],
    })
    export_project_plans(workspace)
    return raw_path, response_payload, response_text


def parse_saved_response_to_bundle(workspace: Workspace, request_id: str, response_text: str | None = None) -> Path:
    store = RequestsStore(workspace)
    manifest = Manifest(workspace)
    clog = ChangeLog(workspace)
    data = store.load()
    request = data["requests"][request_id]

    if response_text is None:
        raw_rel = request.get("raw_response_file")
        if not raw_rel:
            raise RuntimeError("Request has no raw response file.")
        response_payload = json_load(workspace.updates_root / raw_rel, {})
        response_text = extract_response_text(response_payload)

    obj = extract_json_object_from_text(response_text)
    bundle = normalize_patch_bundle(obj)
    incoming_path = workspace.incoming_dir / f"request_{request_id}_patch_bundle_v{int(request['base_dump_version']):04d}.json"
    json_dump(incoming_path, bundle)

    old_status = request["status"]
    request["status"] = "parsed"
    request["incoming_artifact"] = str(incoming_path.relative_to(workspace.updates_root))
    store.update_request(request_id, request)
    manifest.set_request_state(request_id, request_to_manifest_record(request))
    manifest.append_history(request_id, old_status, "parsed", "Response parsed into patch bundle", {
        "incoming_artifact": request["incoming_artifact"],
    })
    clog.append(request_id, "parsed", "success", "Response parsed into patch bundle", {
        "incoming_artifact": request["incoming_artifact"],
    })
    export_project_plans(workspace)
    return incoming_path


def validate_request_bundle(workspace: Workspace, request_id: str) -> tuple[Path, list[str]]:
    store = RequestsStore(workspace)
    manifest = Manifest(workspace)
    clog = ChangeLog(workspace)
    config = json_load(workspace.config_path, {})
    data = store.load()
    request = data["requests"][request_id]
    incoming_rel = request.get("incoming_artifact")
    if not incoming_rel:
        raise RuntimeError("Request has no incoming artifact to validate.")
    bundle_path = workspace.updates_root / incoming_rel
    bundle = json_load(bundle_path, {})
    allow_delete = bool(config.get("policies", {}).get("allow_delete", False))
    errors = validate_patch_bundle(bundle, workspace.project_root, request_id, int(request["base_dump_version"]), allow_delete=allow_delete)

    report_path = workspace.reports_dir / f"request_{request_id}_validation_report.txt"
    if errors:
        report_text = "VALIDATION FAILED\n\n" + "\n".join(errors)
        new_status = "failed_validate"
        log_status = "failed"
        note = "Patch bundle validation failed"
    else:
        report_text = "VALIDATION PASSED"
        new_status = "validated"
        log_status = "success"
        note = "Patch bundle validated"
    report_path.write_text(report_text, encoding="utf-8")

    old_status = request["status"]
    request["status"] = new_status
    request["validation_report"] = str(report_path.relative_to(workspace.updates_root))
    store.update_request(request_id, request)
    manifest.set_request_state(request_id, request_to_manifest_record(request))
    manifest.append_history(request_id, old_status, new_status, note, {
        "validation_report": request["validation_report"],
    })
    clog.append(request_id, "validated" if not errors else "validation_failed", log_status, note, {
        "validation_report": request["validation_report"],
    })
    export_project_plans(workspace)
    return report_path, errors


def apply_request_bundle(workspace: Workspace, request_id: str, dry_run: bool = False) -> Path:
    store = RequestsStore(workspace)
    manifest = Manifest(workspace)
    clog = ChangeLog(workspace)
    config = json_load(workspace.config_path, {})
    data = store.load()
    request = data["requests"][request_id]
    incoming_rel = request.get("incoming_artifact")
    if not incoming_rel:
        raise RuntimeError("Request has no incoming artifact to apply.")
    bundle = json_load(workspace.updates_root / incoming_rel, {})
    allow_add = bool(config.get("policies", {}).get("allow_add", True))
    allow_delete = bool(config.get("policies", {}).get("allow_delete", False))

    diff_report, _ = apply_patch_bundle(workspace, bundle, dry_run=dry_run, allow_add=allow_add, allow_delete=allow_delete)

    old_status = request["status"]
    request["status"] = "validated" if dry_run else "applied"
    request["apply_report"] = str(diff_report.relative_to(workspace.updates_root))
    store.update_request(request_id, request)
    manifest.set_request_state(request_id, request_to_manifest_record(request))
    manifest.append_history(request_id, old_status, request["status"], "Patch bundle applied" if not dry_run else "Dry-run apply completed", {
        "apply_report": request["apply_report"],
    })
    clog.append(request_id, "applied" if not dry_run else "apply_dry_run", "success", "Patch bundle applied" if not dry_run else "Dry-run apply completed", {
        "apply_report": request["apply_report"],
    })
    export_project_plans(workspace)
    return diff_report


def verify_request(workspace: Workspace, request_id: str) -> Path:
    store = RequestsStore(workspace)
    manifest = Manifest(workspace)
    clog = ChangeLog(workspace)
    config = json_load(workspace.config_path, {})
    data = store.load()
    request = data["requests"][request_id]
    build_cfg = config.get("build", {})
    report = run_verification(
        workspace,
        build_cfg.get("build_cmd", ""),
        build_cfg.get("test_cmd", ""),
        build_cfg.get("run_cmd", ""),
    )
    report_text = report.read_text(encoding="utf-8", errors="replace")
    success = "EXIT CODE: 0" in report_text or not report_text.strip()

    old_status = request["status"]
    request["status"] = "verified" if success else "failed_verify"
    request["verify_report"] = str(report.relative_to(workspace.updates_root))
    store.update_request(request_id, request)
    manifest.set_request_state(request_id, request_to_manifest_record(request))
    if success:
        manifest.set_latest_successful_request(request_id)
    manifest.append_history(request_id, old_status, request["status"], "Verification completed", {
        "verify_report": request["verify_report"],
    })
    clog.append(request_id, "verified" if success else "verify_failed", "success" if success else "failed", "Verification completed", {
        "verify_report": request["verify_report"],
    })
    export_project_plans(workspace)
    return report


def find_existing_config_path(initial_project_root: Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if initial_project_root:
        root = initial_project_root.resolve()
        candidates.extend([
            root / UPDATES_DIR_NAME / CONFIG_FILE,
            root / CONFIG_FILE,
        ])
    script_dir = Path(__file__).resolve().parent
    cwd = Path.cwd().resolve()
    for base in [script_dir, cwd, script_dir.parent, cwd.parent]:
        candidates.extend([
            base / CONFIG_FILE,
            base / UPDATES_DIR_NAME / CONFIG_FILE,
        ])
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists() and resolved.is_file():
            return resolved
    return None


def request_is_locked(status: str) -> bool:
    return status in {
        "submitted", "response_received", "parsed", "validated", "applied", "verified",
        "failed_submit", "failed_parse", "failed_validate", "failed_apply", "failed_verify",
    }

# ======================================================================================
# GUI
# ======================================================================================

def launch_gui(initial_project_root: Path | None = None) -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except Exception as exc:
        fail(f"Tkinter is not available: {exc}")
        return 1

    class App:
        def __init__(self, root: tk.Tk) -> None:
            self.root = root
            self.root.title("Project Update Orchestrator")
            self.root.geometry("1400x920")
            try:
                self.root.state("zoomed")
            except Exception:
                pass

            self.task_queue: queue.Queue[tuple[str, int, str, str]] = queue.Queue()
            self.is_running = False
            self.workspace: Workspace | None = None

            self.project_root_var = tk.StringVar(value=str(initial_project_root) if initial_project_root else "")
            self.model_name_var = tk.StringVar(value="gpt-5.4-mini")
            self.api_key_var = tk.StringVar(value="")
            self.build_cmd_var = tk.StringVar()
            self.test_cmd_var = tk.StringVar()
            self.run_cmd_var = tk.StringVar()
            self.allow_add_var = tk.BooleanVar(value=True)
            self.allow_delete_var = tk.BooleanVar(value=False)
            self.status_var = tk.StringVar(value="Ready")

            self.selected_request_id: str | None = None

            # request editor vars
            self.title_var = tk.StringVar()
            self.status_choice_var = tk.StringVar(value="queued")
            self.priority_var = tk.StringVar(value="medium")
            self.type_var = tk.StringVar(value="feature")
            self.base_dump_var = tk.StringVar(value="")
            self.raw_response_var = tk.StringVar(value="")
            self.incoming_artifact_var = tk.StringVar(value="")

            self._build_ui()
            self.root.after(100, self._poll_queue)
            if initial_project_root:
                self._init_workspace()
            else:
                self._autoload_workspace_from_config()

        def _build_ui(self) -> None:
            self.root.columnconfigure(0, weight=1)
            self.root.rowconfigure(1, weight=1)
            self.root.rowconfigure(2, weight=1)

            top = ttk.LabelFrame(self.root, text="Workspace")
            top.grid(row=0, column=0, sticky="ew", padx=10, pady=8)
            top.columnconfigure(1, weight=1)

            ttk.Label(top, text="Project Root").grid(row=0, column=0, sticky="w", padx=6, pady=6)
            ttk.Entry(top, textvariable=self.project_root_var).grid(row=0, column=1, sticky="ew", padx=6, pady=6)
            ttk.Button(top, text="Browse", command=self._browse_project_root).grid(row=0, column=2, padx=6, pady=6)
            ttk.Button(top, text="Init Workspace", command=self._init_workspace).grid(row=0, column=3, padx=6, pady=6)
            ttk.Button(top, text="Export Plans", command=self._export_plans).grid(row=0, column=4, padx=6, pady=6)

            ttk.Label(top, text="Model").grid(row=1, column=0, sticky="w", padx=6, pady=6)
            ttk.Entry(top, textvariable=self.model_name_var).grid(row=1, column=1, sticky="ew", padx=6, pady=6)
            ttk.Label(top, text="API Key").grid(row=1, column=2, sticky="w", padx=6, pady=6)
            self.api_key_entry = ttk.Entry(top, textvariable=self.api_key_var, width=30, show="*")
            self.api_key_entry.grid(row=1, column=3, sticky="ew", padx=6, pady=6)
            ttk.Button(top, text="Save Config", command=self._save_config_from_gui).grid(row=1, column=4, padx=6, pady=6)

            ttk.Label(top, text="Build Cmd").grid(row=2, column=0, sticky="w", padx=6, pady=6)
            ttk.Entry(top, textvariable=self.build_cmd_var).grid(row=2, column=1, sticky="ew", padx=6, pady=6)
            ttk.Label(top, text="Test Cmd").grid(row=2, column=2, sticky="w", padx=6, pady=6)
            ttk.Entry(top, textvariable=self.test_cmd_var).grid(row=2, column=3, sticky="ew", padx=6, pady=6)
            ttk.Label(top, text="Run Cmd").grid(row=3, column=0, sticky="w", padx=6, pady=6)
            ttk.Entry(top, textvariable=self.run_cmd_var).grid(row=3, column=1, sticky="ew", padx=6, pady=6)
            ttk.Checkbutton(top, text="Allow Add", variable=self.allow_add_var).grid(row=3, column=2, sticky="w", padx=6, pady=6)
            ttk.Checkbutton(top, text="Allow Delete", variable=self.allow_delete_var).grid(row=3, column=3, sticky="w", padx=6, pady=6)

            middle = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
            middle.grid(row=1, column=0, sticky="nsew", padx=10, pady=6)

            left = ttk.Frame(middle)
            right = ttk.Frame(middle)
            middle.add(left, weight=3)
            middle.add(right, weight=4)

            # Queue panel
            queue_frame = ttk.LabelFrame(left, text="Request Queue")
            queue_frame.pack(fill="both", expand=True)
            queue_frame.columnconfigure(0, weight=1)
            queue_frame.rowconfigure(0, weight=1)

            cols = ("id", "title", "status", "priority", "type", "updated", "base_dump")
            self.tree = ttk.Treeview(queue_frame, columns=cols, show="headings", selectmode="browse")
            for col, width in {
                "id": 60, "title": 250, "status": 100, "priority": 80,
                "type": 90, "updated": 150, "base_dump": 90,
            }.items():
                self.tree.heading(col, text=col.replace("_", " ").title())
                self.tree.column(col, width=width, anchor="w")
            self.tree.grid(row=0, column=0, sticky="nsew")
            tree_scroll = ttk.Scrollbar(queue_frame, orient="vertical", command=self.tree.yview)
            tree_scroll.grid(row=0, column=1, sticky="ns")
            self.tree.configure(yscrollcommand=tree_scroll.set)
            self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

            btns = ttk.Frame(queue_frame)
            btns.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
            for text, cmd in [
                ("New", self._new_request), ("Duplicate", self._duplicate_request), ("Delete", self._delete_request),
                ("Up", lambda: self._move_request(-1)), ("Down", lambda: self._move_request(1)),
                ("Refresh", self._refresh_requests),
            ]:
                ttk.Button(btns, text=text, command=cmd).pack(side="left", padx=3)

            # Editor panel
            editor = ttk.LabelFrame(right, text="Request Editor")
            editor.pack(fill="both", expand=True)
            editor.columnconfigure(1, weight=1)
            editor.columnconfigure(3, weight=1)

            ttk.Label(editor, text="Request ID").grid(row=0, column=0, sticky="w", padx=6, pady=4)
            self.request_id_label = ttk.Label(editor, text="-")
            self.request_id_label.grid(row=0, column=1, sticky="w", padx=6, pady=4)
            ttk.Label(editor, text="Base Dump").grid(row=0, column=2, sticky="w", padx=6, pady=4)
            ttk.Entry(editor, textvariable=self.base_dump_var, state="readonly").grid(row=0, column=3, sticky="ew", padx=6, pady=4)

            ttk.Label(editor, text="Raw Response File").grid(row=1, column=0, sticky="w", padx=6, pady=4)
            ttk.Entry(editor, textvariable=self.raw_response_var, state="readonly").grid(row=1, column=1, columnspan=3, sticky="ew", padx=6, pady=4)

            ttk.Label(editor, text="Incoming Artifact").grid(row=2, column=0, sticky="w", padx=6, pady=4)
            ttk.Entry(editor, textvariable=self.incoming_artifact_var, state="readonly").grid(row=2, column=1, columnspan=3, sticky="ew", padx=6, pady=4)

            ttk.Label(editor, text="Title").grid(row=3, column=0, sticky="w", padx=6, pady=4)
            self.title_entry = ttk.Entry(editor, textvariable=self.title_var)
            self.title_entry.grid(row=3, column=1, columnspan=3, sticky="ew", padx=6, pady=4)

            ttk.Label(editor, text="Status").grid(row=4, column=0, sticky="w", padx=6, pady=4)
            self.status_combo = ttk.Combobox(editor, textvariable=self.status_choice_var, values=REQUEST_STATUSES, state="readonly")
            self.status_combo.grid(row=4, column=1, sticky="ew", padx=6, pady=4)
            ttk.Label(editor, text="Priority").grid(row=4, column=2, sticky="w", padx=6, pady=4)
            self.priority_combo = ttk.Combobox(editor, textvariable=self.priority_var, values=REQUEST_PRIORITIES, state="readonly")
            self.priority_combo.grid(row=4, column=3, sticky="ew", padx=6, pady=4)

            ttk.Label(editor, text="Type").grid(row=5, column=0, sticky="w", padx=6, pady=4)
            self.type_combo = ttk.Combobox(editor, textvariable=self.type_var, values=REQUEST_TYPES, state="readonly")
            self.type_combo.grid(row=5, column=1, sticky="ew", padx=6, pady=4)

            self.user_goal_text = self._add_labeled_text(editor, 6, "User Goal")
            self.constraints_text = self._add_labeled_text(editor, 7, "Technical Constraints")
            self.files_areas_text = self._add_labeled_text(editor, 8, "Files or Areas")
            self.acceptance_text = self._add_labeled_text(editor, 9, "Acceptance Criteria")
            self.notes_text = self._add_labeled_text(editor, 10, "Implementation Notes")
            self.prompt_append_text = self._add_labeled_text(editor, 11, "Model Prompt Append")

            action_frame = ttk.Frame(editor)
            action_frame.grid(row=12, column=0, columnspan=4, sticky="ew", pady=8)
            self.save_request_button = ttk.Button(action_frame, text="Save Request", command=self._save_request)
            self.save_request_button.pack(side="left", padx=3)
            for text, cmd in [
                ("Export Dump", self._export_dump_for_selected),
                ("Build Prompt", self._build_prompt_for_selected),
                ("Submit API", self._submit_for_selected),
                ("Import Response File", self._import_response_file_for_selected),
                ("Parse Response", self._parse_response_for_selected),
                ("Validate", self._validate_for_selected),
                ("Apply (Dry Run)", lambda: self._apply_for_selected(True)),
                ("Apply", lambda: self._apply_for_selected(False)),
                ("Verify", self._verify_for_selected),
            ]:
                ttk.Button(action_frame, text=text, command=cmd).pack(side="left", padx=3)

            bottom = ttk.LabelFrame(self.root, text="Output Log")
            bottom.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
            bottom.columnconfigure(0, weight=1)
            bottom.rowconfigure(0, weight=1)
            self.log_text = tk.Text(bottom, wrap="word", height=14)
            self.log_text.grid(row=0, column=0, sticky="nsew")
            log_scroll = ttk.Scrollbar(bottom, orient="vertical", command=self.log_text.yview)
            log_scroll.grid(row=0, column=1, sticky="ns")
            self.log_text.configure(yscrollcommand=log_scroll.set)
            ttk.Label(self.root, textvariable=self.status_var, anchor="w").grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))

        def _add_labeled_text(self, parent, row: int, label: str):
            parent.rowconfigure(row, weight=1)
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="nw", padx=6, pady=4)
            txt = tk.Text(parent, height=4, wrap="word")
            txt.grid(row=row, column=1, columnspan=3, sticky="nsew", padx=6, pady=4)
            return txt

        def _append_log(self, text: str) -> None:
            self.log_text.insert("end", text)
            self.log_text.see("end")

        def _browse_project_root(self) -> None:
            path = filedialog.askdirectory(title="Select Project Root")
            if path:
                self.project_root_var.set(path)

        def _autoload_workspace_from_config(self) -> None:
            config_path = find_existing_config_path(initial_project_root)
            if not config_path:
                return
            config = json_load(config_path, {})
            project_root = str(config.get("project_root", "")).strip()
            if not project_root:
                return
            root_path = Path(project_root)
            if not root_path.exists() or not root_path.is_dir():
                return
            self.project_root_var.set(str(root_path))
            self.workspace = Workspace(root_path)
            self.workspace.initialize()
            self._load_config_into_gui()
            export_project_plans(self.workspace)
            self._refresh_requests()
            self.status_var.set(f"Workspace ready: {self.workspace.updates_root}")
            self._append_log(f"Auto-loaded workspace from config: {config_path}\n")

        def _set_editor_lock_state(self, locked: bool) -> None:
            entry_state = "disabled" if locked else "normal"
            text_state = "disabled" if locked else "normal"
            self.title_entry.configure(state=entry_state)
            self.status_combo.configure(state="disabled" if locked else "readonly")
            self.priority_combo.configure(state="disabled" if locked else "readonly")
            self.type_combo.configure(state="disabled" if locked else "readonly")
            for widget in [
                self.user_goal_text,
                self.constraints_text,
                self.files_areas_text,
                self.acceptance_text,
                self.notes_text,
                self.prompt_append_text,
            ]:
                widget.configure(state=text_state)
            self.save_request_button.configure(state="disabled" if locked else "normal")

        def _init_workspace(self) -> None:
            raw = self.project_root_var.get().strip()
            if not raw:
                messagebox.showerror("Missing Project Root", "Select a project root first.")
                return
            project_root = Path(raw)
            if not project_root.exists() or not project_root.is_dir():
                messagebox.showerror("Invalid Project Root", "Project root must be an existing directory.")
                return
            self.workspace = Workspace(project_root)
            self.workspace.initialize()
            self._load_config_into_gui()
            export_project_plans(self.workspace)
            self._refresh_requests()
            self.status_var.set(f"Workspace ready: {self.workspace.updates_root}")
            self._append_log(f"Initialized workspace: {self.workspace.updates_root}\n")

        def _load_config_into_gui(self) -> None:
            if not self.workspace:
                return
            config = json_load(self.workspace.config_path, {})
            self.project_root_var.set(config.get("project_root", str(self.workspace.project_root)))
            self.model_name_var.set(config.get("model", {}).get("model_name", self.model_name_var.get()))
            self.api_key_var.set(config.get("api", {}).get("api_key", self.api_key_var.get()))
            self.build_cmd_var.set(config.get("build", {}).get("build_cmd", ""))
            self.test_cmd_var.set(config.get("build", {}).get("test_cmd", ""))
            self.run_cmd_var.set(config.get("build", {}).get("run_cmd", ""))
            self.allow_add_var.set(bool(config.get("policies", {}).get("allow_add", True)))
            self.allow_delete_var.set(bool(config.get("policies", {}).get("allow_delete", False)))

        def _save_config_from_gui(self) -> None:
            if not self.workspace:
                messagebox.showerror("No Workspace", "Initialize the workspace first.")
                return
            config = json_load(self.workspace.config_path, {})
            config["project_root"] = str(self.workspace.project_root)
            config["updates_root"] = str(self.workspace.updates_root)
            config["model"]["model_name"] = self.model_name_var.get().strip()
            config["project_root"] = str(self.workspace.project_root)
            config["updates_root"] = str(self.workspace.updates_root)
            config.setdefault("api", {})["api_key"] = self.api_key_var.get().strip()
            config["build"]["build_cmd"] = self.build_cmd_var.get().strip()
            config["build"]["test_cmd"] = self.test_cmd_var.get().strip()
            config["build"]["run_cmd"] = self.run_cmd_var.get().strip()
            config["policies"]["allow_add"] = self.allow_add_var.get()
            config["policies"]["allow_delete"] = self.allow_delete_var.get()
            json_dump(self.workspace.config_path, config)
            self._append_log(f"Saved config: {self.workspace.config_path}\n")

        def _export_plans(self) -> None:
            if not self.workspace:
                return
            export_project_plans(self.workspace)
            self._append_log(f"Exported project plans: {self.workspace.project_plans_path}\n")

        def _refresh_requests(self) -> None:
            if not self.workspace:
                return
            for item in self.tree.get_children():
                self.tree.delete(item)
            data = RequestsStore(self.workspace).load()
            for rid in data["queue_order"]:
                req = data["requests"][rid]
                self.tree.insert("", "end", iid=rid, values=(
                    rid,
                    req.get("title", ""),
                    req.get("status", ""),
                    req.get("priority", ""),
                    req.get("type", ""),
                    req.get("updated", ""),
                    req.get("base_dump_version", ""),
                ))

        def _on_tree_select(self, event=None) -> None:
            if not self.workspace:
                return
            sel = self.tree.selection()
            if not sel:
                return
            rid = sel[0]
            self.selected_request_id = rid
            data = RequestsStore(self.workspace).load()
            req = data["requests"][rid]
            self.request_id_label.config(text=rid)
            self.title_var.set(req.get("title", ""))
            self.status_choice_var.set(req.get("status", "queued"))
            self.priority_var.set(req.get("priority", "medium"))
            self.type_var.set(req.get("type", "feature"))
            self.base_dump_var.set(str(req.get("base_dump_version", "")))
            self.raw_response_var.set(req.get("raw_response_file", ""))
            self.incoming_artifact_var.set(req.get("incoming_artifact", ""))
            self._set_editor_lock_state(False)
            self._set_text(self.user_goal_text, req.get("user_goal", ""))
            self._set_text(self.constraints_text, req.get("technical_constraints", ""))
            self._set_text(self.files_areas_text, req.get("files_or_areas", ""))
            self._set_text(self.acceptance_text, req.get("acceptance_criteria", ""))
            self._set_text(self.notes_text, req.get("implementation_notes", ""))
            self._set_text(self.prompt_append_text, req.get("model_prompt_append", ""))
            self._set_editor_lock_state(request_is_locked(req.get("status", "")))

        def _set_text(self, widget, text: str) -> None:
            prev_state = str(widget.cget("state"))
            if prev_state == "disabled":
                widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.insert("1.0", text)
            if prev_state == "disabled":
                widget.configure(state="disabled")

        def _get_text(self, widget) -> str:
            return widget.get("1.0", "end").rstrip()

        def _new_request(self) -> None:
            if not self.workspace:
                return
            store = RequestsStore(self.workspace)
            req = store.create_request()
            ChangeLog(self.workspace).append(req["id"], "created", "success", "Request created in GUI")
            export_project_plans(self.workspace)
            self._refresh_requests()
            self.tree.selection_set(req["id"])
            self._on_tree_select()

        def _duplicate_request(self) -> None:
            if not self.workspace or not self.selected_request_id:
                return
            req = RequestsStore(self.workspace).duplicate_request(self.selected_request_id)
            ChangeLog(self.workspace).append(req["id"], "duplicated", "success", f"Duplicated from {self.selected_request_id}")
            export_project_plans(self.workspace)
            self._refresh_requests()
            self.tree.selection_set(req["id"])
            self._on_tree_select()

        def _delete_request(self) -> None:
            if not self.workspace or not self.selected_request_id:
                return
            rid = self.selected_request_id
            if not messagebox.askyesno("Delete Request", f"Delete request {rid}?"):
                return
            RequestsStore(self.workspace).delete_request(rid)
            ChangeLog(self.workspace).append(rid, "deleted", "success", "Request deleted")
            export_project_plans(self.workspace)
            self.selected_request_id = None
            self._refresh_requests()

        def _move_request(self, direction: int) -> None:
            if not self.workspace or not self.selected_request_id:
                return
            RequestsStore(self.workspace).reorder(self.selected_request_id, direction)
            export_project_plans(self.workspace)
            self._refresh_requests()
            self.tree.selection_set(self.selected_request_id)

        def _save_request(self) -> None:
            if not self.workspace or not self.selected_request_id:
                return
            current = RequestsStore(self.workspace).load()["requests"][self.selected_request_id]
            if request_is_locked(current.get("status", "")):
                messagebox.showwarning("Request Locked", "Submitted or processed requests are read-only. Duplicate the request to edit it.")
                return
            fields = {
                "title": self.title_var.get().strip(),
                "status": self.status_choice_var.get().strip(),
                "priority": self.priority_var.get().strip(),
                "type": self.type_var.get().strip(),
                "user_goal": self._get_text(self.user_goal_text),
                "technical_constraints": self._get_text(self.constraints_text),
                "files_or_areas": self._get_text(self.files_areas_text),
                "acceptance_criteria": self._get_text(self.acceptance_text),
                "implementation_notes": self._get_text(self.notes_text),
                "model_prompt_append": self._get_text(self.prompt_append_text),
            }
            RequestsStore(self.workspace).update_request(self.selected_request_id, fields)
            ChangeLog(self.workspace).append(self.selected_request_id, "updated", "success", "Request edited in GUI")
            export_project_plans(self.workspace)
            self._refresh_requests()
            self.tree.selection_set(self.selected_request_id)
            self._on_tree_select()

        def _run_task(self, title: str, func) -> None:
            if self.is_running:
                messagebox.showwarning("Busy", "Another operation is already running.")
                return
            self.is_running = True
            self.status_var.set(f"Running: {title}")
            self._append_log(f"\n=== {title} started ===\n")
            def worker():
                out = io.StringIO()
                err = io.StringIO()
                rc = 0
                try:
                    with redirect_stdout(out), redirect_stderr(err):
                        rc = int(func())
                except Exception:
                    import traceback
                    traceback.print_exc(file=err)
                    rc = 1
                self.task_queue.put((title, rc, out.getvalue(), err.getvalue()))
            threading.Thread(target=worker, daemon=True).start()

        def _poll_queue(self) -> None:
            while True:
                try:
                    title, rc, stdout_text, stderr_text = self.task_queue.get_nowait()
                except queue.Empty:
                    break
                self.is_running = False
                if stdout_text:
                    self._append_log(stdout_text)
                    if not stdout_text.endswith("\n"):
                        self._append_log("\n")
                if stderr_text:
                    self._append_log(stderr_text)
                    if not stderr_text.endswith("\n"):
                        self._append_log("\n")
                self.status_var.set(f"{title} finished with exit code {rc}")
                self._append_log(f"=== {title} finished with exit code {rc} ===\n\n")
                self._refresh_requests()
            self.root.after(100, self._poll_queue)

        def _export_dump_for_selected(self) -> None:
            if not self.workspace or not self.selected_request_id:
                return
            def job():
                req, dump_path, dump_version, prompt_path = prepare_request(self.workspace, self.selected_request_id)
                ok(f"Prepared request {self.selected_request_id}")
                info(f"Dump: {dump_path}")
                info(f"Prompt: {prompt_path}")
                return 0
            self._run_task("Prepare Request", job)

        def _build_prompt_for_selected(self) -> None:
            self._export_dump_for_selected()

        def _submit_for_selected(self) -> None:
            if not self.workspace or not self.selected_request_id:
                return
            def job():
                raw_path, _, _ = submit_request_via_api(self.workspace, self.selected_request_id)
                ok(f"Submitted request {self.selected_request_id}")
                info(f"Raw response: {raw_path}")
                return 0
            self._save_config_from_gui()
            self._run_task("Submit Request", job)

        def _import_response_file_for_selected(self) -> None:
            if not self.workspace or not self.selected_request_id:
                return
            path = filedialog.askopenfilename(title="Select raw response text or JSON file")
            if not path:
                return
            src = Path(path)
            dst = self.workspace.responses_dir / f"request_{self.selected_request_id}_imported_response.txt"
            shutil.copy2(src, dst)
            data = RequestsStore(self.workspace).load()
            req = data["requests"][self.selected_request_id]
            req["raw_response_file"] = str(dst.relative_to(self.workspace.updates_root))
            req["status"] = "response_received"
            RequestsStore(self.workspace).update_request(self.selected_request_id, req)
            ChangeLog(self.workspace).append(self.selected_request_id, "response_received", "success", "Imported response file", {
                "raw_response_file": req["raw_response_file"],
            })
            export_project_plans(self.workspace)
            self._append_log(f"Imported response file: {dst}\n")
            self._refresh_requests()

        def _parse_response_for_selected(self) -> None:
            if not self.workspace or not self.selected_request_id:
                return
            def job():
                data = RequestsStore(self.workspace).load()
                req = data["requests"][self.selected_request_id]
                raw_rel = req.get("raw_response_file")
                if not raw_rel:
                    raise RuntimeError("No raw response file available.")
                raw_path = self.workspace.updates_root / raw_rel
                if raw_path.suffix.lower() == ".json":
                    payload = json_load(raw_path, {})
                    response_text = extract_response_text(payload)
                else:
                    response_text = raw_path.read_text(encoding="utf-8", errors="replace")
                incoming = parse_saved_response_to_bundle(self.workspace, self.selected_request_id, response_text=response_text)
                ok(f"Parsed response to {incoming}")
                return 0
            self._run_task("Parse Response", job)

        def _validate_for_selected(self) -> None:
            if not self.workspace or not self.selected_request_id:
                return
            def job():
                report, errors = validate_request_bundle(self.workspace, self.selected_request_id)
                info(f"Validation report: {report}")
                if errors:
                    for e in errors:
                        fail(e)
                    return 1
                ok("Validation passed")
                return 0
            self._run_task("Validate Bundle", job)

        def _apply_for_selected(self, dry_run: bool) -> None:
            if not self.workspace or not self.selected_request_id:
                return
            def job():
                report = apply_request_bundle(self.workspace, self.selected_request_id, dry_run=dry_run)
                info(f"Apply report: {report}")
                ok("Apply completed")
                return 0
            self._save_config_from_gui()
            self._run_task("Apply Bundle" if not dry_run else "Apply Bundle (Dry Run)", job)

        def _verify_for_selected(self) -> None:
            if not self.workspace or not self.selected_request_id:
                return
            def job():
                report = verify_request(self.workspace, self.selected_request_id)
                info(f"Verify report: {report}")
                ok("Verification completed")
                return 0
            self._save_config_from_gui()
            self._run_task("Verify", job)

    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0

# ======================================================================================
# CLI
# ======================================================================================

def cli_init(args) -> int:
    ws = Workspace(Path(args.project_root))
    ws.initialize()
    export_project_plans(ws)
    ok(f"Initialized workspace at {ws.updates_root}")
    return 0


def cli_export(args) -> int:
    ws = Workspace(Path(args.project_root))
    ws.initialize()
    out = export_project_dump(ws, include_hidden=args.include_hidden, max_file_size_mb=args.max_file_size_mb)
    ok(f"Exported dump: {out}")
    return 0


def cli_gui(args) -> int:
    return launch_gui(Path(args.project_root) if args.project_root else None)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Project Update Orchestrator")
    sub = p.add_subparsers(dest="cmd")

    init_p = sub.add_parser("init", help="Initialize _PROJECT_UPDATES under a project root")
    init_p.add_argument("project_root")

    exp_p = sub.add_parser("export", help="Create a versioned project dump")
    exp_p.add_argument("project_root")
    exp_p.add_argument("--include-hidden", action="store_true")
    exp_p.add_argument("--max-file-size-mb", type=float, default=2.0)

    gui_p = sub.add_parser("gui", help="Launch GUI")
    gui_p.add_argument("project_root", nargs="?")

    return p


def main() -> int:
    if len(sys.argv) == 1:
        return launch_gui()
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.cmd == "init":
            return cli_init(args)
        if args.cmd == "export":
            return cli_export(args)
        if args.cmd == "gui":
            return cli_gui(args)
        parser.print_help()
        return 0
    except KeyboardInterrupt:
        fail("Interrupted.")
        return 130
    except urllib.error.HTTPError as exc:
        fail(f"HTTPError: {exc.code} {exc.reason}\n{exc.read().decode('utf-8', errors='replace')}")
        return 1
    except Exception as exc:
        fail(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
