# Claude Code Project Memory

## Project

`job_ftch` is a pre-alpha async vacancy ingestion pipeline. It collects raw vacancy-like input from Telegram channels, Telegram groups, Telegram post comments, career sites, and local fixtures; sanitizes it; quarantines malformed or suspicious input; and writes JSON output.

Current runtime output is sanitized `RawItem` JSON, not normalized `Job` output. `Job` exists as a domain target schema for later extraction work.

## Read First

- `docs/vision.md`: project purpose and target users.
- `docs/architecture.md`: hexagonal architecture, ports, layers, data flow.
- `docs/tech_stack.md`: dependencies and rationale.
- `docs/rules.md`: development workflow and verification gates.
- `docs/adr/`: accepted architecture decisions.
- `.serena/memories/CORE-01-INDEX.md`: compact current project knowledge index.

## Architecture

- `domain/`: pure Pydantic models and enums. No I/O. No imports outside stdlib and `pydantic`.
- `application/`: Protocol ports, pipeline orchestration, rejection conversion, logging, telemetry.
- `infrastructure/`: adapters for fixture files, Telegram, career-site HTTP/HTML, in-memory store, and PostgreSQL store.
- `nodes/`: processing nodes. Current runtime chain is `SanitizeNode`, `ValidateRawNode`, and `OriginPolicyNode`.
- `sinks/`: output adapters. Currently only `JsonFileSink`.
- `app.py`: composition root and CLI entry point.
- `config.py`: `JOB_FTCH_` settings, backend enums, and runtime validation.

## Hard Rules

- `SanitizeNode` must be first in every pipeline chain.
- Do not commit secrets, `.env`, Telethon sessions, runtime outputs, caches, or generated junk.
- Add dependencies only after updating `docs/tech_stack.md`.
- Add an ADR in `docs/adr/` before non-trivial architecture changes.
- Keep changes surgical and covered by tests matching the touched scope.
- Allowed commit types: `feat`, `fix`, `chore`, `docs`, `refactor`.

## Repo Agent Policy

Agent instruction docs and Serena memories are tracked in `main` for this project. Do not create or publish a separate `fullrepo` branch. Do not install fullrepo exclude rules that hide `AGENTS.md`, `.claude/`, or `.serena/` from normal git status.

## Common Commands

- `uv sync`: install/sync dependencies.
- `uv run python app.py`: run the local pipeline.
- `uv run ruff check .`: lint.
- `uv run ruff format --check .`: formatting check.
- `uv run mypy domain application infrastructure nodes sinks config.py`: CI-scoped type check.
- `uv run pytest tests/`: run tests.
- `uv run bandit -r domain application infrastructure nodes sinks app.py config.py -ll`: CI security scan.

## Configuration

Settings use `pydantic-settings`, env prefix `JOB_FTCH_`, and env files `.env` plus `.env.dev`.

Supported source backends: `local_fixture`, `telegram_channel`, `telegram_group`, `telegram_comment`, `career_site`.
Supported sink backend: `json_file`.
Supported store backends: `memory`, `postgres`.

Telegram backends require `JOB_FTCH_TELEGRAM_API_ID`, `JOB_FTCH_TELEGRAM_API_HASH`, and `JOB_FTCH_TELEGRAM_ENTITY`. Career-site backend requires `JOB_FTCH_CAREER_SITE_URL`, HTTPS, and a host allowed by `JOB_FTCH_CAREER_SITE_ALLOWED_HOSTS`.

## Current Behavior To Preserve

- `Pipeline.__init__` rejects empty node chains and node chains whose first node is not sanitize.
- `Pipeline.run` tracks fetched/source records, sanitized, dropped, emitted, quarantined, failed, extracted, duplicates, and per-stage/reason/source counters.
- Source-level `QuarantinedRawItem` records bypass nodes and go to quarantine output.
- `RawItemRejected` from nodes is converted to `QuarantinedRawItem`.
- `SanitizeNode` performs deterministic sanitation and validation-safe reconstruction. `ValidateRawNode` enforces practical raw limits such as max text length and required locator. `OriginPolicyNode` validates Telegram URL hosts, career-site allowlisted hosts, and private/local career-site hosts.
- Local fixture source supports JSON arrays and JSONL, and quarantines invalid records instead of stopping the run.
- `PostgresStore` persists processed IDs, dedup keys, source cursors, jobs, run summaries, and rejection payloads through `psycopg`.

## Verification Notes

CI uses Python 3.12 through `astral-sh/setup-uv@v5`. Local global Python may not have project dependencies installed; prefer `uv run` commands over plain `python`.

If changing source adapters, run `uv run pytest tests/test_sources.py`.
If changing sanitizer or quarantine behavior, run `uv run pytest tests/test_input_hygiene.py tests/test_app_e2e.py`.
If changing pipeline ordering or sink behavior, run `uv run pytest tests/test_pipeline.py`.
