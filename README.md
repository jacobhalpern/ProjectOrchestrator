# ProjectOrchestrator

ProjectOrchestrator is a local Python scaffold for controlling staged, LLM-assisted C++ development.

Its first-phase purpose is to:

- create a project control folder inside a target C++ repository;
- create JSON/Markdown task packets for Codex or another implementation agent;
- generate strict Codex handoff prompts;
- validate changed files against task packet scope;
- run configured build/test commands;
- save validation reports.

This scaffold intentionally does **not** implement autonomous Codex execution, automatic commits, automatic merges, or complex C++ semantic analysis.

## Verify the generated application

```bash
python -m compileall project_orchestrator
python -m project_orchestrator --help
```

## Initialize a controlled target project

```bash
python -m project_orchestrator init-control-folder --target C:/Path/To/CppProject
```

## Create a task packet

```bash
python -m project_orchestrator create-task ^
  --target C:/Path/To/CppProject ^
  --task-id STAGE_01_TASK_001_core_models ^
  --stage-id STAGE_01 ^
  --title "Implement core models" ^
  --objective "Create the initial C++ core model skeleton." ^
  --allowed include/MyProject/core/Model.hpp src/core/Model.cpp tests/core_tests/ModelTests.cpp ^
  --expected include/MyProject/core/Model.hpp src/core/Model.cpp tests/core_tests/ModelTests.cpp
```

## Export a Codex prompt

```bash
python -m project_orchestrator export-codex-prompt --target C:/Path/To/CppProject --task-id STAGE_01_TASK_001_core_models
```

## Validate after implementation

```bash
python -m project_orchestrator validate-task --target C:/Path/To/CppProject --task-id STAGE_01_TASK_001_core_models
```

Use `--skip-commands` to run file-scope checks without running build/test commands.

## Optional GUI shell

```bash
pip install -e .[gui]
python -m project_orchestrator.gui.main_window
```
