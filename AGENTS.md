# Agent Instructions for ProjectOrchestrator

## Hard Rules

- Keep business logic out of GUI classes.
- Services must be usable from the CLI without importing PySide6.
- Persistent project artifacts must remain JSON or Markdown.
- Do not add autonomous Codex execution in this phase.
- Do not add automatic git commit or merge behavior.
- Do not hide validation failures.
- Validators must return structured results instead of only printing text.
- Prefer deterministic templates over free-form generated project structure.

## Architecture Boundaries

- `domain/` contains data objects only.
- `services/` contains workflow logic.
- `validators/` contains deterministic checks.
- `adapters/` contains external system access.
- `gui/` calls services; it does not own application logic.
- `cli/` calls services; it does not duplicate workflow logic.
