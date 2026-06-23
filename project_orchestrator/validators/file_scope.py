from __future__ import annotations

import fnmatch
from pathlib import Path

from project_orchestrator.domain.models import ValidationResult


def matches_any(path: str, patterns: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    for pattern in patterns:
        pat = pattern.replace("\\", "/")
        if normalized == pat or fnmatch.fnmatch(normalized, pat):
            return True
    return False


class AllowedFilesValidator:
    name = "AllowedFilesValidator"

    def validate(self, changed_files: list[str], allowed_files: list[str]) -> ValidationResult:
        if not changed_files:
            return ValidationResult.passed_result(self.name, "No changed files detected.")
        if not allowed_files:
            return ValidationResult.failed_result(self.name, "No allowed files were specified.", changed_files)
        outside_scope = [path for path in changed_files if not matches_any(path, allowed_files)]
        if outside_scope:
            return ValidationResult.failed_result(self.name, "Files outside the allowed file list were modified.", outside_scope)
        return ValidationResult.passed_result(self.name, "All changed files are within the allowed file list.", changed_files)


class ForbiddenFilesValidator:
    name = "ForbiddenFilesValidator"

    def validate(self, changed_files: list[str], forbidden_files: list[str]) -> ValidationResult:
        touched = [path for path in changed_files if matches_any(path, forbidden_files)]
        if touched:
            return ValidationResult.failed_result(self.name, "Forbidden files were modified.", touched)
        return ValidationResult.passed_result(self.name, "No forbidden files were modified.")


class ExpectedFilesValidator:
    name = "ExpectedFilesValidator"

    def validate(self, project_root: Path, expected_files: list[str]) -> ValidationResult:
        missing = [path for path in expected_files if not (project_root / path).exists()]
        if missing:
            return ValidationResult.failed_result(self.name, "Expected files are missing.", missing)
        return ValidationResult.passed_result(self.name, "All expected files exist.")
