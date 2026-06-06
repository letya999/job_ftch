<!-- Memory Metadata
Last updated: 2026-06-06
Last commit: 35f604c feat(persistence): add PostgreSQL store backend
Scope: tests/, .github/workflows/ci.yml, pyproject.toml
Area: TEST
-->

# TEST-01-QUALITY-GATES

## Purpose

Document tests and verification gates that prove current repository behavior.

## Source Of Truth

- `tests/`: unit, adapter, pipeline, source, sink, config, and E2E tests.
- `.github/workflows/ci.yml`: CI quality gates.
- `pyproject.toml`: pytest, ruff, mypy, and bandit configuration.

## Entry Points

- `uv run pytest tests/`: full test suite.
- `uv run ruff check .`: lint.
- `uv run ruff format --check .`: formatting.
- `uv run mypy .`: full type check.
- `uv run bandit -r app.py config.py application domain infrastructure nodes sinks -ll`: security scan.

## Current Behavior

`tests/test_postgres_store.py` covers PostgreSQL persistence behavior with a
fake psycopg-style connection, so the default suite does not require a live
database.

`tests/test_config.py` verifies that PostgreSQL backend selection requires a
DSN and allows an explicit DSN.

## Contracts And Data

CI uses `uv` and Python 3.12. Local commands should use `uv run` instead of
plain `python` so dependencies come from the project environment.

## Invariants

- No fake green checks.
- No live credentials are required for the default test suite.
- Security checks must not scan committed secrets because none should exist.

## Change Rules

- Add or update tests in the same slice as behavior changes.
- If a check cannot run, record the exact blocker.

## Verification

- `uv run pytest tests/test_postgres_store.py`: PostgreSQL store contract.
- `uv run pytest tests/test_config.py`: settings and env policy.
- Full gate: lint, format-check, mypy, pytest, and bandit.
