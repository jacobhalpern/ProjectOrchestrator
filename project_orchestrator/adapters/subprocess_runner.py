from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class CommandResult:
    command: str
    return_code: int
    stdout: str
    stderr: str

    @property
    def succeeded(self) -> bool:
        return self.return_code == 0

    @property
    def combined_output(self) -> str:
        if self.stderr:
            return f"{self.stdout}\n[stderr]\n{self.stderr}".strip()
        return self.stdout.strip()


class SubprocessRunner:
    def run(self, command: str, cwd: Path) -> CommandResult:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            shell=True,
            text=True,
            capture_output=True,
        )
        return CommandResult(command, completed.returncode, completed.stdout, completed.stderr)
