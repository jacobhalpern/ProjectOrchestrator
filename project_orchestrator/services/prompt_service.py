from __future__ import annotations

from project_orchestrator.domain.models import TaskPacket


class PromptService:
    def render_codex_prompt(self, packet: TaskPacket) -> str:
        def bullet(items: list[str], code: bool = False) -> str:
            if not items:
                return "- None specified"
            if code:
                return "\n".join(f"- `{item}`" for item in items)
            return "\n".join(f"- {item}" for item in items)

        return f"""# Codex Implementation Request

You are implementing one bounded task in an existing C++ project.

## Task ID
{packet.task_id}

## Stage
{packet.stage_id}

## Objective
{packet.objective}

## Hard Constraints
- Modify only the files listed under Allowed Files.
- Do not modify files listed under Forbidden Files.
- Do not add third-party dependencies unless explicitly required by this task.
- Do not remove, skip, or weaken tests to make validation pass.
- Report any deviation from the task.

## Allowed Files
{bullet(packet.allowed_files)}

## Forbidden Files
{bullet(packet.forbidden_files)}

## Required Behavior
{bullet(packet.required_behavior)}

## Required Tests
{bullet(packet.required_tests)}

## Validation Commands
{bullet(packet.validation_commands, code=True)}

## Acceptance Criteria
{bullet(packet.acceptance_criteria)}

## Required Final Response
After implementation, report:
1. Files changed
2. Tests added or updated
3. Commands run
4. Build/test result
5. Deviations from this task
6. Remaining risks
"""
