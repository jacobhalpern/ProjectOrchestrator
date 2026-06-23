from __future__ import annotations

from pathlib import Path

from project_orchestrator.domain.models import ProjectProfile, Stage, StagePlan
from project_orchestrator.persistence.json_store import JsonStore
from project_orchestrator.services.paths import control_dir


class ProjectService:
    def initialize_control_folder(self, target_root: Path, project_name: str | None = None) -> ProjectProfile:
        target_root = target_root.resolve()
        base = control_dir(target_root)
        for child in ["design_docs", "manifests", "task_packets", "validation_reports"]:
            (base / child).mkdir(parents=True, exist_ok=True)

        profile = ProjectProfile(project_name=project_name or target_root.name, project_root=str(target_root))
        stage_plan = StagePlan(stages=[
            Stage("STAGE_00", "Repository Foundation", "draft", [], "Create deterministic project structure, build system, and test harness."),
            Stage("STAGE_01", "Core Domain Models", "draft", ["STAGE_00"], "Implement stable value types and core project data structures."),
        ])
        JsonStore.write(base / "project_profile.json", profile.to_dict())
        JsonStore.write(base / "stage_plan.json", stage_plan.to_dict())
        self._write_default_design_docs(base)
        self._write_default_manifests(base)
        return profile

    def load_profile(self, target_root: Path) -> ProjectProfile:
        return ProjectProfile.from_dict(JsonStore.read(control_dir(target_root) / "project_profile.json"))

    def _write_default_design_docs(self, base: Path) -> None:
        docs = {
            "00_project_charter.md": "# Project Charter\n\nDefine project purpose, non-goals, platforms, and constraints.\n",
            "01_architecture.md": "# Architecture\n\nDefine modules, dependencies, and runtime boundaries.\n",
            "02_module_boundaries.md": "# Module Boundaries\n\nDefine ownership and dependency direction.\n",
            "03_build_system.md": "# Build System\n\nDefine CMake targets, generators, and build profiles.\n",
            "04_testing_strategy.md": "# Testing Strategy\n\nDefine unit, smoke, integration, and validation tests.\n",
            "05_stage_plan.md": "# Stage Plan\n\nDefine staged implementation sequence.\n",
            "06_llm_development_rules.md": "# LLM Development Rules\n\n- Modify only files listed in the task packet.\n- Do not add dependencies without approval.\n- Do not remove tests to make builds pass.\n- Report all deviations from the task.\n",
        }
        for name, content in docs.items():
            path = base / "design_docs" / name
            if not path.exists():
                path.write_text(content, encoding="utf-8")

    def _write_default_manifests(self, base: Path) -> None:
        manifests = {
            "expected_tree.json": {"expected_directories": ["include", "src", "tests", "docs"]},
            "file_ownership.json": {"owners": []},
            "allowed_dependencies.json": {"allowed_dependencies": ["C++ Standard Library"]},
            "forbidden_dependencies.json": {"forbidden_dependencies": []},
            "public_interfaces.json": {"public_headers": []},
        }
        for name, data in manifests.items():
            path = base / "manifests" / name
            if not path.exists():
                JsonStore.write(path, data)
