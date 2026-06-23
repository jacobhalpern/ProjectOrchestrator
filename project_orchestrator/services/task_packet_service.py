from __future__ import annotations

from pathlib import Path

from project_orchestrator.domain.models import TaskPacket
from project_orchestrator.persistence.json_store import JsonStore
from project_orchestrator.services.paths import task_dir
from project_orchestrator.services.prompt_service import PromptService


class TaskPacketService:
    def create_task_packet(self, project_root: Path, packet: TaskPacket) -> Path:
        folder = task_dir(project_root, packet.task_id)
        folder.mkdir(parents=True, exist_ok=True)
        JsonStore.write(folder / "task.json", packet.to_dict())
        JsonStore.write(folder / "allowed_files.json", {"allowed_files": packet.allowed_files})
        JsonStore.write(folder / "validation_commands.json", {"validation_commands": packet.validation_commands})
        (folder / "task.md").write_text(self.render_task_markdown(packet), encoding="utf-8")
        (folder / "codex_prompt.md").write_text(PromptService().render_codex_prompt(packet), encoding="utf-8")
        return folder

    def load_task_packet(self, project_root: Path, task_id: str) -> TaskPacket:
        return TaskPacket.from_dict(JsonStore.read(task_dir(project_root, task_id) / "task.json"))

    def render_task_markdown(self, packet: TaskPacket) -> str:
        def bullet(items: list[str]) -> str:
            return "\n".join(f"- {item}" for item in items) if items else "- None specified"

        return f"""# Task Packet: {packet.task_id}

## Stage
{packet.stage_id}

## Title
{packet.title}

## Objective
{packet.objective}

## Context
{bullet(packet.context)}

## Allowed Files
{bullet(packet.allowed_files)}

## Forbidden Files
{bullet(packet.forbidden_files)}

## Expected Files
{bullet(packet.expected_files)}

## Required Behavior
{bullet(packet.required_behavior)}

## Required Tests
{bullet(packet.required_tests)}

## Validation Commands
{bullet(packet.validation_commands)}

## Acceptance Criteria
{bullet(packet.acceptance_criteria)}
"""
