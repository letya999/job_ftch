<!-- Memory Metadata
Last updated: 2026-06-06
Last commit: 074ae27 feat(pipeline): add PostgreSQL-backed foundation
Scope: app.py, config.py, pyproject.toml, uv.lock, .env.example, .env.dev.example, .env.prod.example, docs/configuration.md, application/logging.py, application/telemetry.py, .gitignore
Area: CLI
-->

# CLI-01-CONFIG-RUNTIME

## Purpose

Document runtime configuration, CLI overrides, dependencies, and observability setup.

## Source Of Truth

- `config.py`: `Settings`, backend enums, environment loading and validation.
- `app.py`: CLI args and composition root.
- `.env.example`, `.env.dev.example`, `.env.prod.example`: documented environment keys.
- `docs/configuration.md`: env var reference with defaults, required/optional state, and secret classification.
- `uv.lock`: committed dependency lockfile for reproducible CLI/application installs.
- `pyproject.toml`: package metadata, dependencies, and tool config.
- `application/logging.py`: structlog JSON logging setup.
- `application/telemetry.py`: OpenTelemetry provider setup.
- `.gitignore`: runtime and generated artifact policy.

## Entry Points

- `Settings()`: loads environment variables with prefix `JOB_FTCH_` from `.env` and `.env.dev`.
- `app.py:parse_args`: defines CLI overrides.
- `app.py:build_settings`: applies CLI overrides by validating a merged settings payload.
- `app.py:build_nodes`: wires `SanitizeNode`, `ValidateRawNode`, and `OriginPolicyNode` using runtime settings.
- `app.py:build_store`: selects memory or PostgreSQL store.
- `configure_logging`: emits JSON logs to stderr through structlog.
- `configure_telemetry`: installs an OpenTelemetry `TracerProvider` once.

## Current Behavior

CLI flags are `--source-backend`, `--source-path`, `--telegram-entity`, `--career-site-url`, `--output-path`, `--jsonl`, and `--max-items`.

Source backends are `local_fixture`, `telegram_channel`, `telegram_group`, `telegram_comment`, and `career_site`. Sink backend is `json_file`. Store backend is `memory` or `postgres`.

Telegram backends require `JOB_FTCH_TELEGRAM_API_ID`, `JOB_FTCH_TELEGRAM_API_HASH`, and `JOB_FTCH_TELEGRAM_ENTITY`. Career-site backend requires `JOB_FTCH_CAREER_SITE_URL`, enforces HTTPS, and restricts the host to `JOB_FTCH_CAREER_SITE_ALLOWED_HOSTS` through `OriginPolicyNode` at runtime.

MVP runtime settings reserve explicit contracts for jobs, rejected items, review items, run summaries, PostgreSQL DSN, dry-run, raw text length, dedup threshold, HTTP retry/timeout guards, LLM backend/model/base URL/API key, LLM retry/timeout/call guards, and extraction quality thresholds. `JOB_FTCH_MAX_TEXT_LENGTH` is enforced by `ValidateRawNode`. `JOB_FTCH_CAREER_SITE_ALLOWED_HOSTS` is passed into `OriginPolicyNode`. `JOB_FTCH_STORE_BACKEND=postgres` selects `PostgresStore` and requires `JOB_FTCH_POSTGRES_DSN`.

The current runtime still emits debug `RawItem` output; rejected/review/summary production sinks and LLM adapters are later slices.

Comma-separated tuple settings use `pydantic_settings.NoDecode` plus explicit validators so env values such as `JOB_FTCH_TELEGRAM_ENTITIES=a,b` load as tuples instead of JSON.

## Contracts And Data

Important `JOB_FTCH_` keys:

- `SOURCE_BACKEND`, `SINK_BACKEND`, `STORE_BACKEND`
- `LOG_LEVEL`, `TELEMETRY_SERVICE_NAME`, `TELEMETRY_CONSOLE_EXPORTER`
- `PIPELINE_MAX_ITEMS_PER_RUN`
- `PIPELINE_MAX_SOURCE_ERRORS`, `DRY_RUN`, `MAX_TEXT_LENGTH`, `DEDUP_THRESHOLD`
- `DEBUG_SOURCE_PATH`
- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_PATH`, `TELEGRAM_ENTITY`, `TELEGRAM_ENTITIES`
- `TELEGRAM_MESSAGE_LIMIT`, `TELEGRAM_COMMENT_POST_LIMIT`, `TELEGRAM_COMMENT_LIMIT_PER_POST`
- `TELEGRAM_HISTORY_WAIT_TIME_SECONDS`, `TELEGRAM_FLOOD_SLEEP_THRESHOLD_SECONDS`
- `HTTP_TIMEOUT_SECONDS`, `HTTP_MAX_RETRIES`, `HTTP_MAX_PAGES_PER_SOURCE`
- `CAREER_SITE_URL`, `CAREER_SITE_URLS`, `CAREER_SITE_CONFIG_PATH`, `CAREER_SITE_ALLOWED_HOSTS`
- `OUTPUT_PATH`, `OUTPUT_JSONL`
- `QUARANTINE_OUTPUT_PATH`, `QUARANTINE_OUTPUT_JSONL`
- `JOBS_OUTPUT_PATH`, `JOBS_OUTPUT_JSONL`
- `REJECTED_OUTPUT_PATH`, `REJECTED_OUTPUT_JSONL`
- `REVIEW_OUTPUT_PATH`, `REVIEW_OUTPUT_JSONL`
- `RUN_SUMMARY_OUTPUT_PATH`, `POSTGRES_DSN`
- `LLM_BACKEND`, `LLM_MODEL`, `LLM_BASE_URL`, `LLM_API_KEY`
- `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES`, `LLM_MAX_CALLS_PER_RUN`
- `EXTRACTION_MAIN_QUALITY_THRESHOLD`, `EXTRACTION_REVIEW_QUALITY_THRESHOLD`

Runtime artifacts ignored by `.gitignore` include `.env`, `.env.dev`, `.env.prod`, `.venv/`, Telethon `*.session*`, coverage/build caches, and `artifacts/`. `uv.lock` is intentionally tracked.

## Invariants

- Do not commit credentials or local `.env` files.
- Treat PostgreSQL DSNs as secret-bearing configuration.
- Keep runtime output under ignored paths unless adding a deliberate fixture.
- Keep dependency rationale in `docs/tech_stack.md` when changing `pyproject.toml` dependencies.
- Commit `uv.lock` for dependency graph reproducibility unless maintainers explicitly change the repository policy.

## Change Rules

- Add a backend enum and composition function branch together with config tests.
- If a new environment key is added, update env examples and tests.
- If an external client is added, keep credentials optional unless that backend is selected.
- If an LLM backend is enabled, require backend-specific credentials and model settings through `Settings` validators.

## Verification

- `uv run pytest tests/test_config.py`: settings validation, env examples, MVP output paths, threshold policy, tuple env parsing, and backend-specific policy.
- `uv run pytest tests/test_app_e2e.py`: runtime context propagation and e2e fixture behavior.
- `uv run pytest tests/test_pipeline.py::test_app_runs_local_pipeline_command`: local CLI execution.
- `uv run ruff check app.py config.py application/logging.py application/telemetry.py`: focused lint for runtime wiring.
