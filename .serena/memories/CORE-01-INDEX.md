<!-- Memory Metadata
Last updated: 2026-06-06
Last commit: 35f604c feat(persistence): add PostgreSQL store backend
Scope: AGENTS.md, .claude/CLAUDE.md, docs/, pyproject.toml, uv.lock, app.py, config.py, domain/, application/, infrastructure/, nodes/, sinks/, tests/, .github/workflows/
Area: CORE
-->

# CORE-01-INDEX

## Purpose

Index for durable project knowledge. `job_ftch` is a pre-alpha async vacancy
ingestion pipeline for Telegram, career-site, and fixture inputs.

## Source Of Truth

- `docs/vision.md`: product purpose, target users, and non-goals.
- `docs/architecture.md`: hexagonal architecture, ports, layers, and data flow.
- `docs/tech_stack.md`: dependency choices and rationale.
- `docs/rules.md`: development workflow and verification gates.
- `docs/adr/`: accepted architecture decisions, including PostgreSQL persistence.
- `AGENTS.md`: Codex-native project instructions.
- `.claude/CLAUDE.md`: Claude Code-native project memory.
- `config.py`: `JOB_FTCH_` settings and backend-specific validation.
- `app.py`: composition root and CLI overrides.

## Entry Points

- `uv run python app.py`: run the local pipeline.
- `app.py:build_settings`: merge environment settings with CLI overrides.
- `app.py:run_pipeline`: configure logging/telemetry, build adapters, and run `Pipeline`.
- `.github/workflows/ci.yml`: CI lint, format, type, test, and security gates.

## Current Behavior

The current runtime emits sanitized/triaged `RawItem` records through JSON
output. `Job` exists as a domain target schema for later extraction work. There
is no API server, browser UI, Docker setup, or deployment script in this repo.

Agent instruction docs and Serena memories are tracked directly in this
repository. Do not create or publish a separate `fullrepo` branch for this
project.

## Contracts And Data

Active memory files:

- `CORE-01-INDEX.md`: this index and source-of-truth map.
- `CORE-02-ARCHITECTURE.md`: architecture, ports, layers, and extension rules.
- `DATA-01-DOMAIN-MODELS.md`: domain models, identity, quarantine, and dedup data.
- `BACKEND-02-SOURCES-SINKS.md`: source, sink, and store adapters.
- `CLI-01-CONFIG-RUNTIME.md`: settings, CLI flags, dependency lock, and observability.
- `TEST-01-QUALITY-GATES.md`: tests, CI, and verification commands.

## Invariants

- `domain/` imports only stdlib and `pydantic`.
- `SanitizeNode` is the first executable pipeline stage.
- Backend adapters self-register through `application.registry`.
- No secrets in code, docs, tests, env examples, logs, or memories.
- PostgreSQL DSNs are secret-bearing configuration.

## Change Rules

- Read `docs/vision.md`, `docs/architecture.md`, `docs/tech_stack.md`, and
  `docs/rules.md` before substantial changes.
- Update ADRs before non-trivial architecture changes.
- Keep code, docs, tests, and memories aligned with verified source files.

## Verification

- `uv run ruff check .`: lint.
- `uv run ruff format --check .`: formatting.
- `uv run mypy .`: full type check.
- `uv run pytest tests/`: behavior and regression tests.
- `uv run bandit -r app.py config.py application domain infrastructure nodes sinks -ll`: security scan.
