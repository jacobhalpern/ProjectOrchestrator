from __future__ import annotations

import argparse
from pathlib import Path

from project_orchestrator.domain.models import TaskPacket
from project_orchestrator.services.project_service import ProjectService
from project_orchestrator.services.task_packet_service import TaskPacketService
from project_orchestrator.services.validation_service import ValidationService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="project-orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)

    init_cmd = sub.add_parser("init-control-folder", help="Create project_orchestrator control files in a target project.")
    init_cmd.add_argument("--target", required=True, help="Target C++ project root.")
    init_cmd.add_argument("--project-name", default=None, help="Project display name.")

    task_cmd = sub.add_parser("create-task", help="Create a task packet folder.")
    task_cmd.add_argument("--target", required=True)
    task_cmd.add_argument("--task-id", required=True)
    task_cmd.add_argument("--stage-id", required=True)
    task_cmd.add_argument("--title", required=True)
    task_cmd.add_argument("--objective", required=True)
    task_cmd.add_argument("--context", nargs="*", default=[])
    task_cmd.add_argument("--allowed", nargs="*", default=[])
    task_cmd.add_argument("--forbidden", nargs="*", default=[])
    task_cmd.add_argument("--expected", nargs="*", default=[])
    task_cmd.add_argument("--behavior", nargs="*", default=[])
    task_cmd.add_argument("--tests", nargs="*", default=[])
    task_cmd.add_argument("--commands", nargs="*", default=[])

    prompt_cmd = sub.add_parser("export-codex-prompt", help="Print a generated Codex prompt for a task.")
    prompt_cmd.add_argument("--target", required=True)
    prompt_cmd.add_argument("--task-id", required=True)

    validate_cmd = sub.add_parser("validate-task", help="Validate the current git diff against a task packet.")
    validate_cmd.add_argument("--target", required=True)
    validate_cmd.add_argument("--task-id", required=True)
    validate_cmd.add_argument("--skip-commands", action="store_true", help="Run file validators only; skip build/test commands.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init-control-folder":
        profile = ProjectService().initialize_control_folder(Path(args.target), args.project_name)
        print(f"Initialized ProjectOrchestrator control folder for: {profile.project_name}")
        return 0

    if args.command == "create-task":
        packet = TaskPacket(
            task_id=args.task_id,
            stage_id=args.stage_id,
            title=args.title,
            objective=args.objective,
            context=args.context,
            allowed_files=args.allowed,
            forbidden_files=args.forbidden,
            expected_files=args.expected,
            required_behavior=args.behavior,
            required_tests=args.tests,
            validation_commands=args.commands or ["cmake --build build", "ctest --test-dir build --output-on-failure"],
        )
        folder = TaskPacketService().create_task_packet(Path(args.target), packet)
        print(f"Created task packet: {folder}")
        return 0

    if args.command == "export-codex-prompt":
        project_root = Path(args.target)
        packet = TaskPacketService().load_task_packet(project_root, args.task_id)
        prompt_path = project_root / "project_orchestrator" / "task_packets" / packet.task_id / "codex_prompt.md"
        print(prompt_path.read_text(encoding="utf-8"))
        return 0

    if args.command == "validate-task":
        project_root = Path(args.target)
        packet = TaskPacketService().load_task_packet(project_root, args.task_id)
        report = ValidationService().validate_task(project_root, packet, run_commands=not args.skip_commands)
        print(f"Validation {'PASSED' if report.passed else 'FAILED'} for {packet.task_id}")
        for result in report.results:
            print(f"[{result.status.upper()}] {result.validator}: {result.message}")
            for detail in result.details:
                print(f"  - {detail}")
        return 0 if report.passed else 1

    parser.error("Unhandled command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
