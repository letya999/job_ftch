<!-- Memory Metadata
Last updated: 2026-06-06
Last commit: 074ae27 feat(pipeline): add PostgreSQL-backed foundation
Scope: AGENTS.md, .claude/CLAUDE.md, docs/, pyproject.toml, app.py, config.py, domain/, application/, infrastructure/, nodes/, sinks/, tests/, .github/workflows/
Area: CORE
-->

# CORE-01-INDEX

## Purpose

Index for durable project knowledge. `job_ftch` is a pre-alpha async vacancy ingestion pipeline that collects raw vacancy-like input from Telegram, career sites, and fixtures, sanitizes it, validates raw records, enforces URL/origin policy, quarantines malformed input, and writes structured JSON output.

## Source Of Truth

- `docs/vision.md`: product purpose, target users, v0 goals.
- `docs/architecture.md`: hexagonal architecture target, five ports, layer boundaries, data flow.
- `docs/mvp_implementation_plan.md`: active PR-sized implementation sequence for architecture hardening toward MVP readiness.
- `docs/configuration.md`: runtime env reference and lockfile policy.
- `docs/rules.md`: development rules, verification commands, dependency and ADR policy.
- `docs/adr/`: accepted architecture decisions.
- `AGENTS.md`: Codex-native project instructions.
- `.claude/CLAUDE.md`: Claude Code-native project memory.
- `pyproject.toml`: Python version, package layout, dependencies, ruff/mypy/pytest/bandit config.
- `app.py`: composition root and CLI overrides.
- `config.py`: settings model, env prefix, backend enums, runtime validation.
- `domain/`: Pydantic domain and quarantine data models.
- `application/`: ports, processing context, node outcomes, pipeline engine, run summary, logging, telemetry, rejection conversion.
- `infrastructure/`: source adapters plus in-memory and PostgreSQL stores.
- `nodes/`: processing nodes; current runtime chain is `SanitizeNode -> ValidateRawNode -> OriginPolicyNode`.
- `sinks/`: output adapters; currently `JsonFileSink`.
- `tests/`: verified behavior and regression fixtures.

## Entry Points

- `uv run python app.py`: run the local pipeline composition root.
- `app.py:build_settings`: merges environment settings with CLI overrides.
- `app.py:run_pipeline`: configures logging/telemetry, builds adapters/nodes/store, and runs `Pipeline`.
- `.github/workflows/ci.yml`: CI lint, format, type, test, and security gates.

## Current Behavior

The implemented runtime path emits sanitized and raw-policy-checked `RawItem` records, not `Job` records. `Job` is present as a target domain schema for later extraction work. There is no API server, browser UI, Docker setup, or deployment script in this repository.

Agent instruction docs and Serena memories are tracked directly in this repository. Do not create or publish a separate `fullrepo` branch for this project.

Batch 1 of the master refactor is implemented: nodes return structured `NodeOutcome` values, the pipeline tracks stage/reason/source counters, sinks finalize at the end of a run, and JSON run summaries serialize datetimes as ISO 8601 strings. Pipeline orchestration remains in `application/pipeline.py`; outcome/quarantine/failure/finalize handlers live in `application/pipeline_handlers.py`.

Current hardening slices implemented after Batch 1:

- Settings/output contract readiness: MVP output paths, dry-run, max text length, dedup threshold, PostgreSQL DSN, HTTP/LLM timeout and retry knobs, LLM backend/model/API-key policy, and extraction quality thresholds are explicit.
- Raw protection nodes: `SanitizeNode` handles Unicode/whitespace/control-character and URL-shape normalization, `ValidateRawNode` enforces raw usefulness and locator limits, and `OriginPolicyNode` enforces Telegram/career-site URL origin policy.
- Persistent store foundation: `Store` now includes atomic processed-ID/dedup-key, source cursor, run summary, and rejection persistence methods; `PostgresStore` implements the production persistent backend through `psycopg`.

`docs/mvp_implementation_plan.md` is the active implementation plan for what remains before sustained MVP feature development: source cursor/retry semantics, rejected/review/summary outputs, identity/dedup, job extraction, CLI flow, and release readiness.

## Contracts And Data

Active memory files:

- `CORE-01-INDEX.md`: this index and source-of-truth map.
- `CORE-02-ARCHITECTURE.md`: architecture, layers, boundaries, extension rules.
- `CORE-03-MVP-IMPLEMENTATION-PLAN.md`: current MVP hardening plan and readiness sequence.
- `DATA-01-DOMAIN-MODELS.md`: domain models, stable IDs, quarantine schema.
- `BACKEND-01-PIPELINE-QUARANTINE.md`: ports, pipeline behavior, sanitizer/validation/origin/quarantine flow.
- `BACKEND-02-SOURCES-SINKS.md`: source adapters, sink/store adapters, external integrations.
- `CLI-01-CONFIG-RUNTIME.md`: settings, CLI flags, env keys, observability, packaging.
- `TEST-01-QUALITY-GATES.md`: tests, CI, and verification commands.

## Invariants

- `domain/` imports only stdlib and `pydantic`.
- `SanitizeNode` must be the first node in every `Pipeline`.
- No secrets in code; credentials are environment-only.
- New dependencies require `docs/tech_stack.md` updates.
- Non-trivial architecture choices require ADRs under `docs/adr/`.
- Do not create or publish a separate `fullrepo` branch for this repository.
- Do not install fullrepo exclude rules that hide `AGENTS.md`, `.claude/`, or `.serena/` from normal git status.

## Change Rules

- Read `docs/vision.md`, `docs/architecture.md`, `docs/tech_stack.md`, and `docs/rules.md` before substantial changes.
- Prefer existing ports and adapter locations over new abstractions.
- Keep code, docs, tests, and memories aligned with verified source files.

## Verification

- `uv run ruff check .`: lint.
- `uv run ruff format --check .`: formatting.
- `uv run mypy domain application infrastructure nodes sinks config.py`: CI-scoped type check.
- `uv run mypy .`: full local type check used for architecture slices.
- `uv run pytest tests/`: behavior and regression tests.
- `uv run bandit -r domain application infrastructure nodes sinks app.py config.py -ll`: security scan used by CI.
