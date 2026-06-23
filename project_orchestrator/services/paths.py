from __future__ import annotations

from pathlib import Path

CONTROL_DIR_NAME = "project_orchestrator"


def control_dir(project_root: Path) -> Path:
    return project_root / CONTROL_DIR_NAME


def task_dir(project_root: Path, task_id: str) -> Path:
    return control_dir(project_root) / "task_packets" / task_id


def validation_report_dir(project_root: Path, task_id: str) -> Path:
    return control_dir(project_root) / "validation_reports" / task_id
