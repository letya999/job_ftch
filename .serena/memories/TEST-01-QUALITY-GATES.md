<!-- Memory Metadata
Last updated: 2026-06-06
Last commit: 074ae27 feat(pipeline): add PostgreSQL-backed foundation
Scope: pyproject.toml, .github/workflows/ci.yml, .github/workflows/release.yml, tests/, docs/rules.md, CONTRIBUTING.md
Area: TEST
-->

# TEST-01-QUALITY-GATES

## Purpose

Document official verification commands and current test coverage areas.

## Source Of Truth

- `pyproject.toml`: ruff, mypy, pytest, coverage, and bandit config.
- `.github/workflows/ci.yml`: CI jobs and exact CI command scopes.
- `.github/workflows/release.yml`: tag-triggered GitHub Release workflow.
- `docs/rules.md`: required checks before commit.
- `CONTRIBUTING.md`: PR rules and verification expectations.
- `tests/`: behavior and regression coverage.

## Entry Points

- `uv run ruff check .`: lint.
- `uv run ruff format --check .`: format check.
- `uv run mypy domain application infrastructure nodes sinks config.py`: CI type check scope.
- `uv run pytest tests/ --cov --cov-report=xml -v`: CI test command with coverage XML.
- `uv run bandit -r domain application infrastructure nodes sinks app.py config.py -ll`: CI security scan.

## Current Behavior

Test coverage includes domain model invariants, Protocol runtime checks, settings validation, env example alignment, in-memory and PostgreSQL store behavior, pipeline semantics, outcome factories, run summary serialization, JSON sink finalization, sanitizer, raw validation, origin policy, quarantine behavior, e2e fixture runs, Telegram adapter mapping, and career-site parser mapping.

CI runs on pushes and pull requests targeting `main` or `dev`. Release workflow creates GitHub Releases for tags matching `v*`.

## Contracts And Data

`pyproject.toml` declares Python `>=3.12`, flat package layout, strict mypy, ruff target `py312`, and test path `tests`.

`tests/test_app_e2e.py` verifies that the positive multisource fixture emits records across Telegram channel, Telegram group, Telegram comment, and career-site source kinds. It also verifies negative quarantine flows and that `JOB_FTCH_MAX_TEXT_LENGTH` reaches `ProcessingContext` and `ValidateRawNode`.

`tests/test_config.py` covers default MVP output path settings, prefixed env overrides, comma-separated tuple env parsing, OpenAI LLM backend credential requirements, extraction threshold ordering, PostgreSQL store backend selection, and `.env*.example` key alignment with `Settings.model_fields`.

`tests/test_postgres_store.py` covers `PostgresStore` behavior through a fake psycopg-style connection, so the default test suite does not require a live database.

## Invariants

- Use `uv` for dependency and command execution.
- Keep tests offline by relying on fixtures/fakes for Telegram, HTTP, PostgreSQL, and LLM integrations.
- Add or update tests when changing domain validation, source mapping, sanitizer policy, raw validation, origin policy, store protocol, pipeline ordering, or output contracts.

## Change Rules

- Dependency changes require `docs/tech_stack.md` updates.
- Significant architecture changes require an ADR in `docs/adr/`.
- Commits and PR titles use the documented Conventional Commits subset.

## Verification

- `uv run ruff check .`: catches lint/import/style issues selected by project ruff config.
- `uv run ruff format --check .`: verifies formatting without modifying files.
- `uv run mypy domain application infrastructure nodes sinks config.py`: matches CI type-check scope.
- `uv run mypy .`: full local type check used for architecture slices.
- `uv run pytest tests/`: runs all local tests.
- `uv run bandit -r domain application infrastructure nodes sinks app.py config.py -ll`: matches CI security scan scope.
- `uv lock --check`: verifies tracked `uv.lock` matches `pyproject.toml`.
