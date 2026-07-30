<!-- Memory Metadata
Last updated: 2026-06-17
Last commit: f9fc8b8 fix(classifier): remove false-positive announcement tokens
Scope: .serena/memories/suggested_commands.md
Area: CORE
-->

# job_ftch — Suggested Commands

## Dev & Run (Windows, uv-based)
- `uv run job_ftch pipeline` — run extraction pipeline
- `uv run job_ftch pipeline --daemon` — run in background loop
- `uv run job_ftch pipeline --status` — show last run status
- `uv run job_ftch runs` — inspect persisted run history
- `uv run job_ftch search <query>` — search stored vacancies
- `uv run job_ftch status` — show status
- `uv run job_ftch dedup` — dedup operations

## Lint & Format
- `uv run ruff check .` — lint
- `uv run ruff format --check .` — format check
- `uv run ruff format .` — auto-format

## Type Checking
- `uv run mypy .` — strict mypy (config in pyproject.toml)

## Testing (agent-friendly patterns)
- `uv run pytest tests/test_<module>.py -q -o addopts="" --tb=line` — targeted test, quiet
- `uv run pytest -q -o addopts="" --tb=short > .pytest.out 2>&1; tail -n 20 .pytest.out` — full suite, file output
- `uv run pytest tests/ -m "not e2e and not network and not telegram"` — skip network-dependent
- **NEVER** run full suite in verbose (`-v`) in foreground during agent edit loops

## Security
- `uv run bandit -r job_ftch scripts/check_module_boundaries.py -ll` — security lint

## Module Boundaries
- `python scripts/check_module_boundaries.py` — verify no forbidden cross-layer imports
- Manual check: `grep -r "from infrastructure" domain/ application/ nodes/ sinks/` → must be empty

## Telegram Auth
- `python scripts/auth_telethon.py` — authenticate Telethon session

## E2E / Probes
- `python scripts/e2e_probe.py` — end-to-end probe
- `python scripts/live_probe.py` — live source probe
- `python scripts/run_diagnostics.py` — run diagnostics

## Schema Export
- `python scripts/export_schema.py` — export JSON schema of domain models
