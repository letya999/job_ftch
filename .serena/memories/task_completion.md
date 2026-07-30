<!-- Memory Metadata
Last updated: 2026-06-17
Last commit: f9fc8b8 fix(classifier): remove false-positive announcement tokens
Scope: .serena/memories/task_completion.md
Area: CORE
-->

# job_ftch — Task Completion Checklist

## Before considering a coding task done, run ALL of these:

### 1. Lint
```bash
uv run ruff check .
```
Zero errors required.

### 2. Format Check
```bash
uv run ruff format --check .
```
Zero violations required.

### 3. Type Check
```bash
uv run mypy .
```
Zero errors required.

### 4. Module Boundaries
```bash
python scripts/check_module_boundaries.py
```
Must pass — no forbidden imports from `domain/`, `application/`, `nodes/`, `sinks/` into `infrastructure/`.

### 5. Security Lint
```bash
uv run bandit -r job_ftch scripts/check_module_boundaries.py -ll
```
No high-severity findings.

### 6. Targeted Tests (during development loop)
```bash
uv run pytest tests/test_<module>.py -q -o addopts="" --tb=line
```
Must pass for touched modules.

### 7. Full Test Suite (before commit)
```bash
uv run pytest -q -o addopts="" --tb=short > .pytest.out 2>&1; tail -n 20 .pytest.out
```
All tests green.

## Agent-specific rules
- NEVER run full suite with `-v` in foreground during edit loops (burns ~1M tokens).
- Use `-o addopts=""` to override default verbose mode in agent mode.
- For quick feedback: run only affected test file with `--tb=line`.