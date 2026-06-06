<!-- Memory Metadata
Last updated: 2026-06-06
Last commit: 35f604c feat(persistence): add PostgreSQL store backend
Scope: app.py, config.py, pyproject.toml, uv.lock, .env.example, .env.dev.example, .env.prod.example, docs/configuration.md, application/logging.py, application/telemetry.py, .gitignore
Area: CLI
-->

# CLI-01-CONFIG-RUNTIME

## Purpose

Document runtime configuration, CLI overrides, dependencies, and observability.

## Source Of Truth

- `config.py`: `Settings`, environment loading, and backend validation.
- `app.py`: CLI args and composition root.
- `.env.example`, `.env.dev.example`, `.env.prod.example`: safe templates.
- `docs/configuration.md`: factual env reference.
- `pyproject.toml` and `uv.lock`: dependency graph and tool config.

## Entry Points

- `Settings()`: loads `JOB_FTCH_` variables from `.env` and `.env.dev`.
- `app.py:parse_args`: CLI overrides.
- `app.py:build_settings`: validates merged env and CLI settings.
- `app.py:build_store`: creates the registered store backend.

## Current Behavior

CLI flags are `--source-backend`, `--source-path`, `--telegram-entity`,
`--career-site-url`, `--output-path`, `--jsonl`, and `--max-items`.

Source backends are registry keys such as `local_fixture`, `telegram_channel`,
`telegram_group`, `telegram_comment`, and `career_site`. Sink backend is
`json_file`. Store backend is `memory` or `postgres`.

Selecting `store_backend=postgres` requires `postgres_dsn`; the env name is
`JOB_FTCH_POSTGRES_DSN`.

## Contracts And Data

Important `JOB_FTCH_` keys:

- `SOURCE_BACKEND`, `SINK_BACKEND`, `STORE_BACKEND`, `POSTGRES_DSN`
- `LOG_LEVEL`, `TELEMETRY_SERVICE_NAME`, `TELEMETRY_CONSOLE_EXPORTER`
- `PIPELINE_MAX_ITEMS_PER_RUN`
- `DEBUG_SOURCE_PATH`
- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_PATH`, `TELEGRAM_ENTITY`
- `TELEGRAM_MESSAGE_LIMIT`, `TELEGRAM_COMMENT_POST_LIMIT`, `TELEGRAM_COMMENT_LIMIT_PER_POST`
- `TELEGRAM_HISTORY_WAIT_TIME_SECONDS`, `TELEGRAM_FLOOD_SLEEP_THRESHOLD_SECONDS`
- `CAREER_SITE_URL`, `CAREER_SITE_ALLOWED_HOSTS`
- `OUTPUT_PATH`, `OUTPUT_JSONL`, `QUARANTINE_OUTPUT_PATH`, `QUARANTINE_OUTPUT_JSONL`

`uv.lock` is intentionally tracked because this repository is an application/CLI
pipeline.

## Invariants

- Do not commit real `.env` files or secret-bearing DSNs.
- Keep runtime output under ignored `artifacts/`.
- Update docs and env examples when adding a `Settings` field.

## Change Rules

- New dependency requires `docs/tech_stack.md` and lockfile updates.
- New backend-specific credentials should be optional unless that backend is selected.

## Verification

- `uv run pytest tests/test_config.py`: settings validation and backend policy.
- `uv run python app.py --source-path fixtures/debug/raw_items.json --output-path /tmp/raw_items.json --max-items 2`: local smoke when needed.
- `uv run ruff check app.py config.py application/logging.py application/telemetry.py`: focused runtime lint.
