# MVP Implementation Plan

**Status:** active planning document
**Baseline before Slice 1:** `031a312`
**Date:** 2026-06-06

## Purpose

This plan turns the master refactor review into a current, repository-specific
implementation sequence. It is intentionally stricter than the public roadmap:
the roadmap describes product milestones, while this document defines the
architecture readiness work that must happen before broad feature development.

The project should stay small, async, typed, hexagonal, and inspectable. Do not
rewrite the existing spine. Harden it in PR-sized slices.

## Current State After Slice 3

Done:

- Hexagonal boundaries are established: `domain/`, `application/`,
  `infrastructure/`, `nodes/`, `sinks/`, and `app.py`.
- Source adapters exist for local fixtures, Telegram channels, Telegram groups,
  Telegram comments, and career sites.
- Source-level malformed fixture records can become `QuarantinedRawItem`
  records instead of killing the run.
- `NodeOutcome`, `PipelineStage`, `RejectReason`, `ProcessingContext`, and
  `RunSummary` exist.
- `Pipeline` routes structured pass/drop/quarantine/fail outcomes and records
  stage, reason, and source counters.
- `SanitizeNode` returns `NodeOutcome[RawItem]`, reconstructs through Pydantic
  validation, and recomputes `stable_id` after sanitation changes.
- `ValidateRawNode` enforces raw usefulness guards such as max text length and
  required locator.
- `OriginPolicyNode` enforces Telegram host policy, career-site allowlists, and
  local/private career-site host rejection.
- `JsonFileSink` has `finalize()`, UTF-8 JSONL output, and atomic JSON-array
  finalization.
- `PostgresStore` persists processed IDs, dedup keys, source cursors, jobs, run
  summaries, and rejection payloads.
- Environment examples use the `JOB_FTCH_` prefix.
- Slice 1 configuration readiness exists: MVP output paths, dry-run, PostgreSQL
  DSN, LLM knobs, HTTP/LLM retry and timeout knobs, text/dedup/quality
  thresholds, and `docs/configuration.md` are defined.

Partially done:

- `JsonFileSink` is safer, but production output still lacks schema envelopes.
- Quarantine exists, but there is no dedicated rejected-items sink with a stable
  emitted record schema.
- Stage counters exist, but the final summary schema does not yet include all
  MVP conversion metrics.
- Source adapters exist, but source cursor advancement, dry-run semantics, retry
  policy, and source-specific operational guards are not complete.
- `Store` has in-memory and PostgreSQL backends with atomic insert-style APIs, but no
  raw/job dedup node uses them yet.
- `Job` exists, but it is still too small for extraction, review, quality, and
  scoring workflows.

Not started:

- Triage, raw dedup, job dedup, and scoring nodes.
- Job extraction, fake LLM provider, OpenAI/instructor adapter, and extraction
  validation.
- Review output, run-summary file output, and production job envelopes.
- CLI subcommands such as `once`, `inspect-config`, and `validate-fixtures`.
- Release contour docs and sample outputs.

## Non-Negotiable Readiness Gates

Before implementing broad feature work, each touched slice must satisfy:

- `domain/` imports only stdlib and `pydantic`.
- `SanitizeNode` stays first in every executable node chain.
- Node drops, quarantines, and failures use stable `PipelineStage` and
  `RejectReason` values.
- No source adapter stores credentials, phone numbers, raw private user IDs,
  tokens, cookies, or private links in fixtures, logs, docs, or memories.
- New dependencies require `docs/tech_stack.md` updates first.
- Significant architecture choices require ADRs before code.
- Quality gates pass:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest tests/
uv run bandit -r app.py config.py application domain infrastructure nodes sinks -ll
```

## Foundation Completion Plan

### Slice 0 - Documentation And Contract Alignment

Purpose: make project documentation match the current code before more work is
planned from it.

Tasks:

- Update `docs/architecture.md` to describe `NodeOutcome`,
  `ProcessingContext`, `PipelineStage`, and `Sink.finalize()`.
- Update `docs/adr/README.md` so ADR-003 and ADR-004 are indexed.
- Link this plan from the public roadmap or README.

Acceptance:

- A new contributor reading docs sees the same contracts as the code exposes.
- No code changes are required for this slice.

### Slice 1 - Settings And Output Contract Readiness

Status: implemented as configuration contract. The actual PostgreSQL store,
rejected/review/summary sinks, and LLM adapters are still later slices.

Purpose: make runtime configuration explicit enough for the rest of the MVP.

Tasks:

- Add settings for job output, rejected output, review output, run summary
  output, dry-run, max text length, dedup threshold, PostgreSQL DSN, LLM provider,
  LLM model/base URL/API key/enabled flag, timeout, and retry limits.
- Keep all project-owned keys under the `JOB_FTCH_` prefix.
- Add `docs/configuration.md` covering every env var, default, whether it is
  secret, and when it is required.
- Decide lockfile policy. For this CLI/application, prefer committing
  `uv.lock` for reproducible installs unless the maintainer explicitly rejects
  it.

Acceptance:

- `tests/test_config.py` covers defaults, env overrides, backend-specific
  required fields, path settings, thresholds, and redaction-sensitive keys.
- `.env.example`, `.env.dev.example`, and `.env.prod.example` are aligned with
  `Settings`.

### Slice 2 - Raw Protection Nodes

Status: implemented.

Purpose: split raw input protection into explicit pipeline stages instead of
leaving too much policy in `SanitizeNode`.

Tasks:

- Add `nodes/validate_raw.py` for max length, required lineage, and practical
  raw-item usefulness checks.
- Add `nodes/origin_policy.py` for URL scheme, host allowlist, private/local
  address rejection, Telegram host allowance, and metadata URL checks.
- Keep `SanitizeNode` first and limited to deterministic normalization plus
  validation-safe reconstruction.
- Extend `RejectReason` only with stable values that appear in tests and output.

Acceptance:

- `tests/test_validate_raw.py` and `tests/test_origin_policy.py` cover pass and
  rejection paths.
- Pipeline summary and quarantine output expose the exact stage and reason.

### Slice 3 - Production Persistent Idempotency

Status: implemented as store backend and contract. Source cursor advancement,
dry-run mutation policy in sources, and dedup node usage remain later slices.

Purpose: make reruns safe before adding more expensive extraction and posting.

Required ADR:

- `docs/adr/005-postgresql-store-first.md`

Tasks:

- Add `infrastructure/stores/postgres.py` using `psycopg` directly behind the
  `Store` port.
- Add atomic `try_mark_processed` and `try_remember_dedup_key` methods to the
  store contract, while preserving compatibility or migrating callers in one
  controlled slice.
- Persist source cursors, processed raw IDs, dedup keys, jobs, rejections, and
  run summaries as needed for MVP.
- Wire `StoreBackend.POSTGRES` through `config.py` and `app.py`.
- Define dry-run behavior: dry-run must not advance cursors or mark processed
  unless explicitly configured.

Acceptance:

- `tests/test_postgres_store.py` proves atomic inserts, cursor persistence, run
  summary persistence, and rejection persistence through a fake psycopg-style
  connection.
- Two runs against the same PostgreSQL database do not emit duplicate records.

### Slice 4 - Source Cursor And Retry Semantics

Purpose: harden existing source adapters instead of adding new ones from
scratch.

Required ADR:

- `docs/adr/006-source-cursor-and-rerun-semantics.md`

Tasks:

- Define source cursor keys for local fixtures, Telegram channels, Telegram
  groups, Telegram comments, and career sites.
- Advance cursors only after the selected safe processing boundary.
- Add bounded retry and timeout policy for HTTP and Telegram fetches.
- Add source-specific max page/message/error guards.
- Keep integration tests offline with recorded fixtures and fake clients.

Acceptance:

- Source tests prove cursor behavior and safe handling of malformed records.
- Dry-run source execution does not mutate persistent state.

### Slice 5 - Rejected, Review, And Summary Outputs

Purpose: make every non-main path inspectable before extraction starts.

Tasks:

- Add stable rejected-item record schema with `schema_version`, `run_id`,
  `stage`, `reason`, source lineage, limited metadata, text preview, and text
  hash.
- Add `sinks/rejected_jsonl.py`.
- Add `sinks/review_jsonl.py` for borderline future jobs.
- Add `sinks/run_summary.py` for `run_summary.json`.
- Add output envelopes for production jobs, even if the current debug flow still
  emits `RawItem`.
- Decide whether quarantine and rejected output are one sink or two names for
  the same production contract.

Acceptance:

- Dropped, quarantined, duplicate, extraction-failed, sink-failed, and review
  paths can be inspected as JSONL/JSON without reading logs.
- Summary timestamps are ISO 8601 strings.

### Slice 6 - Raw Identity And Dedup Foundation

Purpose: centralize identity before job extraction increases duplicate risk.

Tasks:

- Add `domain/identity.py` for canonical URL, normalized text fingerprints, raw
  IDs, and job IDs.
- Version identity formulas, for example `raw:v1:<sha256>` and
  `job:v1:<sha256>`.
- Define Telegram identity rules using stable message/comment IDs when present.
- Define career-site identity rules using canonical URL first.
- Add raw dedup node using the persistent store.

Acceptance:

- Tests prove stable identity across URL fragments, host casing, whitespace, and
  Telegram edit scenarios with stable external IDs.
- Duplicate records include duplicate kind, matched ID, score when applicable,
  and winning source.

### Slice 7 - Job Schema And Extraction Contract

Purpose: define the `RawItem -> Job` boundary before implementing LLM details.

Required ADR:

- `docs/adr/007-llm-extraction-contract.md`

Tasks:

- Expand `Job` for source lineage, canonical/application URLs, location,
  seniority, employment type, compensation, skills, tags, language, extraction
  confidence, quality score, relevance score, and metadata.
- Keep `company` optional for useful Telegram partials; enforce usefulness in
  job validation, not raw model construction.
- Add `nodes/extract_job.py` with deterministic fallback and fake provider
  tests before live provider work.
- Add `infrastructure/llm/fake.py`.
- Add `infrastructure/llm/openai_instructor.py` only behind `LLMProvider`.
- Add extraction validation and partial-review routing.

Acceptance:

- Obvious local fixtures produce `Job` candidates without live LLM.
- Malformed LLM output, provider timeout, low confidence, and partial extraction
  all have deterministic outcomes.

### Slice 8 - Job Quality, Relevance, And Dedup

Purpose: make emitted jobs useful for the AI/LLM/MLOps niche.

Tasks:

- Add job validation node.
- Add deterministic AI-role relevance scoring.
- Normalize title, company, location, work mode, and compensation.
- Add quality scoring with explainable metadata.
- Add exact URL, exact title/company/location, fingerprint, fuzzy, and
  cross-source job dedup.

Acceptance:

- Relevant AI jobs emit to main output.
- Borderline jobs emit to review.
- Unrelated jobs and low-quality extractions emit to rejected output with
  stable reasons.

### Slice 9 - CLI And Runnable MVP Flow

Purpose: make the project usable from a clean clone.

Tasks:

- Add explicit CLI subcommands:

```bash
uv run python app.py once --source local_fixture --output artifacts/jobs.jsonl
uv run python app.py once --source telegram --max-items 50 --dry-run
uv run python app.py once --source career_site --source-name example --dry-run
uv run python app.py inspect-config
uv run python app.py validate-fixtures
```

- Preserve or document migration from the current debug command.
- Update README with the local no-secret run.
- Add examples for jobs, rejected items, run summary, Telegram source config,
  and career-site source config.

Acceptance:

- From a clean clone, local fixture execution creates valid jobs JSONL, rejected
  JSONL, and run summary JSON.

### Slice 10 - Release Readiness

Purpose: make the MVP maintainable by contributors.

Tasks:

- Add `docs/sources/telegram.md`.
- Add `docs/sources/career_sites.md`.
- Add `docs/troubleshooting.md`.
- Add `docs/release_checklist.md`.
- Add or update contract tests for sources, nodes, sinks, stores, and LLM
  providers.
- Add extraction gold fixtures and offline evaluation harness.

Acceptance:

- A new contributor can clone, configure, run, inspect outputs, troubleshoot,
  run checks, and understand release criteria without private context.

## What Must Not Be Done Yet

- Do not add Kafka, Celery, Airflow, LangChain, LangGraph, Scrapy, or a heavy
  ORM.
- Do not implement posting before idempotent job output exists.
- Do not implement live LLM extraction before the fake provider and extraction
  contract tests pass.
- Do not add broad source crawling before cursor/rerun semantics are documented.
- Do not collapse rejected, review, and main output into an opaque log-only
  path.

## Definition Of Development Ready

The project is ready for sustained feature development when these are true:

- Docs and ADRs match the current runtime contracts.
- Config describes every runtime knob needed by MVP local, dev, and production
  modes.
- PostgreSQL persistence provides idempotent reruns and source cursor storage.
- Main, rejected, review, and summary outputs have stable schemas.
- The pipeline can execute `RawItem -> Job` through a fake deterministic
  extraction path.
- Quality gates pass from a clean clone.
