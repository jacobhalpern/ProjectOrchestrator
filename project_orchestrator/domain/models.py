from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass(slots=True)
class BuildProfile:
    name: str = "Default"
    configure_command: str | None = None
    build_command: str = "cmake --build build"
    test_command: str = "ctest --test-dir build --output-on-failure"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "configure_command": self.configure_command,
            "build_command": self.build_command,
            "test_command": self.test_command,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BuildProfile":
        return cls(
            name=data.get("name", "Default"),
            configure_command=data.get("configure_command"),
            build_command=data.get("build_command", "cmake --build build"),
            test_command=data.get("test_command", "ctest --test-dir build --output-on-failure"),
        )


@dataclass(slots=True)
class ProjectProfile:
    project_name: str
    project_root: str
    language: str = "C++"
    cpp_standard: str = "C++20"
    build_system: str = "CMake"
    test_framework: str = "CTest"
    primary_ide: str = "Visual Studio"
    target_platforms: list[str] = field(default_factory=lambda: ["Windows"])
    allowed_dependencies: list[str] = field(default_factory=lambda: ["C++ Standard Library"])
    forbidden_dependencies: list[str] = field(default_factory=list)
    source_layout: dict[str, str] = field(default_factory=lambda: {
        "include_dir": "include",
        "source_dir": "src",
        "test_dir": "tests",
        "docs_dir": "docs",
    })
    build_profiles: list[BuildProfile] = field(default_factory=lambda: [BuildProfile()])
    agent_policy: dict[str, bool] = field(default_factory=lambda: {
        "require_task_packet": True,
        "require_allowed_files": True,
        "require_validation_before_approval": True,
        "allow_auto_commit": False,
        "allow_auto_merge": False,
    })

    @property
    def root_path(self) -> Path:
        return Path(self.project_root)

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name,
            "project_root": self.project_root,
            "language": self.language,
            "cpp_standard": self.cpp_standard,
            "build_system": self.build_system,
            "test_framework": self.test_framework,
            "primary_ide": self.primary_ide,
            "target_platforms": self.target_platforms,
            "allowed_dependencies": self.allowed_dependencies,
            "forbidden_dependencies": self.forbidden_dependencies,
            "source_layout": self.source_layout,
            "build_profiles": [profile.to_dict() for profile in self.build_profiles],
            "agent_policy": self.agent_policy,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectProfile":
        build_profiles = [BuildProfile.from_dict(item) for item in data.get("build_profiles", [])]
        return cls(
            project_name=data["project_name"],
            project_root=data["project_root"],
            language=data.get("language", "C++"),
            cpp_standard=data.get("cpp_standard", "C++20"),
            build_system=data.get("build_system", "CMake"),
            test_framework=data.get("test_framework", "CTest"),
            primary_ide=data.get("primary_ide", "Visual Studio"),
            target_platforms=list(data.get("target_platforms", ["Windows"])),
            allowed_dependencies=list(data.get("allowed_dependencies", ["C++ Standard Library"])),
            forbidden_dependencies=list(data.get("forbidden_dependencies", [])),
            source_layout=dict(data.get("source_layout", {})) or {
                "include_dir": "include",
                "source_dir": "src",
                "test_dir": "tests",
                "docs_dir": "docs",
            },
            build_profiles=build_profiles or [BuildProfile()],
            agent_policy=dict(data.get("agent_policy", {})) or {
                "require_task_packet": True,
                "require_allowed_files": True,
                "require_validation_before_approval": True,
                "allow_auto_commit": False,
                "allow_auto_merge": False,
            },
        )


@dataclass(slots=True)
class Stage:
    id: str
    name: str
    status: str = "draft"
    depends_on: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "depends_on": self.depends_on,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Stage":
        return cls(
            id=data["id"],
            name=data["name"],
            status=data.get("status", "draft"),
            depends_on=list(data.get("depends_on", [])),
            description=data.get("description", ""),
        )


@dataclass(slots=True)
class StagePlan:
    stages: list[Stage] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"stages": [stage.to_dict() for stage in self.stages]}


@dataclass(slots=True)
class TaskPacket:
    task_id: str
    stage_id: str
    title: str
    objective: str
    context: list[str] = field(default_factory=list)
    allowed_files: list[str] = field(default_factory=list)
    forbidden_files: list[str] = field(default_factory=list)
    expected_files: list[str] = field(default_factory=list)
    required_behavior: list[str] = field(default_factory=list)
    required_tests: list[str] = field(default_factory=list)
    validation_commands: list[str] = field(default_factory=lambda: [
        "cmake --build build",
        "ctest --test-dir build --output-on-failure",
    ])
    acceptance_criteria: list[str] = field(default_factory=lambda: [
        "Only allowed files are modified.",
        "Build succeeds.",
        "Tests pass.",
        "No forbidden dependencies are introduced.",
    ])

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "stage_id": self.stage_id,
            "title": self.title,
            "objective": self.objective,
            "context": self.context,
            "allowed_files": self.allowed_files,
            "forbidden_files": self.forbidden_files,
            "expected_files": self.expected_files,
            "required_behavior": self.required_behavior,
            "required_tests": self.required_tests,
            "validation_commands": self.validation_commands,
            "acceptance_criteria": self.acceptance_criteria,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskPacket":
        return cls(
            task_id=data["task_id"],
            stage_id=data["stage_id"],
            title=data["title"],
            objective=data["objective"],
            context=list(data.get("context", [])),
            allowed_files=list(data.get("allowed_files", [])),
            forbidden_files=list(data.get("forbidden_files", [])),
            expected_files=list(data.get("expected_files", [])),
            required_behavior=list(data.get("required_behavior", [])),
            required_tests=list(data.get("required_tests", [])),
            validation_commands=list(data.get("validation_commands", [])) or [
                "cmake --build build",
                "ctest --test-dir build --output-on-failure",
            ],
            acceptance_criteria=list(data.get("acceptance_criteria", [])) or [
                "Only allowed files are modified.",
                "Build succeeds.",
                "Tests pass.",
                "No forbidden dependencies are introduced.",
            ],
        )


@dataclass(slots=True)
class ValidationResult:
    validator: str
    status: str
    message: str
    details: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status.lower() == "passed"

    def to_dict(self) -> dict:
        return {
            "validator": self.validator,
            "status": self.status,
            "message": self.message,
            "details": self.details,
        }

    @classmethod
    def passed_result(cls, validator: str, message: str, details: list[str] | None = None) -> "ValidationResult":
        return cls(validator=validator, status="passed", message=message, details=details or [])

    @classmethod
    def failed_result(cls, validator: str, message: str, details: list[str] | None = None) -> "ValidationResult":
        return cls(validator=validator, status="failed", message=message, details=details or [])


@dataclass(slots=True)
class ValidationReport:
    task_id: str
    results: list[ValidationResult]
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    command_outputs: dict[str, str] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "generated_at": self.generated_at,
            "passed": self.passed,
            "results": [result.to_dict() for result in self.results],
            "command_outputs": self.command_outputs,
        }
