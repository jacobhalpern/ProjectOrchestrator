from __future__ import annotations

from pathlib import Path

from project_orchestrator.adapters.subprocess_runner import SubprocessRunner


class GitAdapter:
    def __init__(self, runner: SubprocessRunner | None = None) -> None:
        self.runner = runner or SubprocessRunner()

    def changed_files(self, repo_root: Path) -> list[str]:
        commands = [
            "git diff --name-only",
            "git diff --cached --name-only",
            "git ls-files --others --exclude-standard",
        ]
        changed: set[str] = set()
        for command in commands:
            result = self.runner.run(command, repo_root)
            if result.return_code == 0:
                for line in result.stdout.splitlines():
                    value = line.strip().replace("\\", "/")
                    if value:
                        changed.add(value)
        return sorted(changed)

    def diff_patch(self, repo_root: Path) -> str:
        result = self.runner.run("git diff --patch", repo_root)
        return result.stdout
