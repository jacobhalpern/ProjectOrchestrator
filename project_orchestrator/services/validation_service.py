from __future__ import annotations

from pathlib import Path

from project_orchestrator.adapters.git_adapter import GitAdapter
from project_orchestrator.adapters.subprocess_runner import SubprocessRunner
from project_orchestrator.domain.models import TaskPacket, ValidationReport, ValidationResult
from project_orchestrator.persistence.json_store import JsonStore
from project_orchestrator.services.paths import validation_report_dir
from project_orchestrator.validators.file_scope import AllowedFilesValidator, ExpectedFilesValidator, ForbiddenFilesValidator


class ValidationService:
    def __init__(self, git_adapter: GitAdapter | None = None, runner: SubprocessRunner | None = None) -> None:
        self.git_adapter = git_adapter or GitAdapter()
        self.runner = runner or SubprocessRunner()

    def validate_task(self, project_root: Path, packet: TaskPacket, run_commands: bool = True) -> ValidationReport:
        changed = self.git_adapter.changed_files(project_root)
        results: list[ValidationResult] = [
            AllowedFilesValidator().validate(changed, packet.allowed_files),
            ForbiddenFilesValidator().validate(changed, packet.forbidden_files),
            ExpectedFilesValidator().validate(project_root, packet.expected_files),
        ]
        command_outputs: dict[str, str] = {}

        if run_commands:
            for command in packet.validation_commands:
                command_result = self.runner.run(command, project_root)
                command_outputs[command] = command_result.combined_output
                if command_result.succeeded:
                    results.append(ValidationResult.passed_result("CommandValidator", f"Command succeeded: {command}"))
                else:
                    results.append(ValidationResult.failed_result("CommandValidator", f"Command failed: {command}", [command_result.combined_output]))
                    break

        report = ValidationReport(task_id=packet.task_id, results=results, command_outputs=command_outputs)
        self.save_report(project_root, report)
        return report

    def save_report(self, project_root: Path, report: ValidationReport) -> Path:
        folder = validation_report_dir(project_root, report.task_id)
        folder.mkdir(parents=True, exist_ok=True)
        JsonStore.write(folder / "validation_report.json", report.to_dict())
        (folder / "validation_report.md").write_text(self.render_report_markdown(report), encoding="utf-8")
        (folder / "git_diff.patch").write_text(self.git_adapter.diff_patch(project_root), encoding="utf-8")
        return folder

    def render_report_markdown(self, report: ValidationReport) -> str:
        lines = [
            f"# Validation Report: {report.task_id}",
            "",
            f"Generated: {report.generated_at}",
            f"Overall result: {'PASS' if report.passed else 'FAIL'}",
            "",
            "## Checks",
            "",
        ]
        for result in report.results:
            lines.append(f"### {result.validator}: {result.status.upper()}")
            lines.append(result.message)
            if result.details:
                lines.append("")
                lines.extend(f"- {item}" for item in result.details)
            lines.append("")
        return "\n".join(lines)
