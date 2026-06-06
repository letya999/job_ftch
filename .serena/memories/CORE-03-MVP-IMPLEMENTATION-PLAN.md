<!-- Memory Metadata
Last updated: 2026-06-06
Last commit: 074ae27 feat(pipeline): add PostgreSQL-backed foundation
Scope: docs/mvp_implementation_plan.md, docs/configuration.md, docs/roadmap.md, docs/architecture.md, docs/adr/README.md, docs/adr/005-postgresql-store-first.md, config.py, app.py, nodes/, infrastructure/stores/, tests/
Area: CORE
-->

# CORE-03-MVP-IMPLEMENTATION-PLAN

## Purpose

Document the current architecture hardening plan that turns the master refactor review into a repository-specific implementation sequence.

## Source Of Truth

- `docs/mvp_implementation_plan.md`: active implementation order and readiness gates.
- `docs/roadmap.md`: public product milestone map; links to the implementation plan.
- `docs/architecture.md`: current port contracts including `NodeOutcome`, raw protection nodes, PostgreSQL store, and `Sink.finalize()`.
- `docs/adr/README.md`: ADR index including input quarantine, pipeline outcomes, and PostgreSQL store.
- `docs/adr/005-postgresql-store-first.md`: PostgreSQL persistence decision.

## Current Behavior

The master audit report is not used verbatim. It is normalized against current HEAD facts:

- Batch 1 outcome/context/pipeline hardening is implemented.
- Source adapters already exist for local fixtures, Telegram channel/group/comment sources, and career sites.
- Slice 1 settings/output contract readiness is implemented: env examples and `Settings` include MVP output paths, dry-run, PostgreSQL DSN, LLM knobs, HTTP/LLM timeout and retry knobs, raw text/dedup/quality thresholds, and `docs/configuration.md`.
- Slice 2 raw protection nodes are implemented: `SanitizeNode`, `ValidateRawNode`, and `OriginPolicyNode` are wired in `app.py` and covered by tests.
- Slice 3 PostgreSQL persistence foundation is implemented: `Store` has atomic processed/dedup, source cursor, run summary, and rejection persistence methods; `PostgresStore` is a selectable backend.
- `uv.lock` is tracked for reproducible application installs.
- The main runtime still emits debug `RawItem` output, not production `Job` envelopes.

## Plan Sequence

The active development sequence is:

1. Documentation and contract alignment. Done.
2. Settings and output contract readiness. Done.
3. Raw protection nodes. Done.
4. Production PostgreSQL idempotency foundation. Done.
5. Source cursor and retry semantics. Next major implementation slice.
6. Rejected, review, and summary outputs.
7. Raw identity and dedup foundation.
8. Job schema and extraction contract.
9. Job quality, relevance, and dedup.
10. CLI and runnable MVP flow.
11. Release readiness.

## Invariants

- Do not rewrite the existing spine.
- Do not add heavy orchestration frameworks, heavy ORMs, LangChain, LangGraph, Scrapy, Kafka, Celery, or Airflow.
- Do not implement posting before idempotent job output exists.
- Do not implement live LLM extraction before fake-provider extraction tests and the LLM contract ADR exist.
- Do not create or publish a `fullrepo` branch for this repository.

## Verification

After the PostgreSQL persistence update, run these checks before delivery:

- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run mypy .`
- `uv run pytest tests/`
- `uv run bandit -r app.py config.py application domain infrastructure nodes sinks -ll`
- `git diff --check`

## Remaining Gaps

The repository is improved against the review-agent blockers, but the full ideal MVP is not complete. Remaining work includes source cursor advancement and retry policy, rejected/review/run-summary production sinks, raw/job dedup nodes, expanded `Job` schema, extraction and LLM adapters, job quality/relevance scoring, CLI subcommands, examples, and release docs.
