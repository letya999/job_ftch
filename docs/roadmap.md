# Roadmap

## Goal

Build `job_ftch` as a lean MVP pipeline that reaches real-world inputs early, keeps the
core architecture stable, and grows source coverage and output quality incrementally.

For the current architecture hardening sequence, use
[MVP Implementation Plan](mvp_implementation_plan.md). The roadmap remains the
product milestone map; the implementation plan is the source of truth for
PR-sized development order after the Batch 1 outcome refactor.

## Delivery rules

- Real-world contact as early as possible: Telegram channels, Telegram groups/comments,
  and career sites should enter the pipeline early.
- After the core spine, work should split cleanly across 3 parallel streams:
  Telegram, career sites, and cross-cutting pipeline quality.
- Every task should end in working code, tests, and an observable result.
- Avoid speculative infra until the MVP loop is proven.

## Phase 0. Spine

Purpose: create the smallest correct executable core.

### RM-001 Core domain models
- Add `RawItem`, `Job`, and supporting value objects/enums.
- Define stable IDs, serialization, and domain invariants.
- Add unit tests for valid and invalid payloads.

### RM-002 Ports and contracts
- Define `Source`, `Node`, `Sink`, `Store`, and `LLMProvider` protocols.
- Keep `domain/` clean from infrastructure imports.
- Add contract-oriented tests for minimal implementations.

### RM-003 Pipeline engine v1
- Implement async source -> nodes -> sink orchestration.
- Support item drop / pass-through semantics.
- Add a pipeline happy-path integration test.

### RM-004 Config and composition root
- Replace the `app.py` placeholder with a real composition root.
- Harden `Settings` loading from `.env`.
- Add a minimal runnable local pipeline command.

### RM-005 In-memory store
- Implement `InMemoryStore` for processed IDs, dedup keys, and simple run state.
- Add tests for idempotent reads/writes.

### RM-006 Local debug source and JSON sink
- Add a fixture-based local source.
- Add `JsonFileSink` with predictable JSON or JSONL output.
- Prove that the pipeline can run locally with no external services.

### RM-007 Minimal run observability
- Add structured logging and a run summary.
- Log counts for fetched, dropped, emitted, and failed items.

## Phase 1. First real-world ingestion

Purpose: connect the pipeline to actual external data as early as possible.

### RM-008 Source normalization boundary
- Define one canonical `RawItem` shape across all sources.
- Standardize source kind, external ID, URL, timestamps, text, and metadata.

### RM-009 Telegram channel source v1
- Ingest posts from Telegram channels in read-only mode.
- Map raw messages into normalized `RawItem`.
- Add integration tests with recorded samples where possible.

### RM-010 Telegram group source v1
- Ingest posts/messages from Telegram groups in read-only mode.
- Normalize sender/chat/message metadata into `RawItem`.

### RM-011 Telegram comments source v1
- Ingest comments to channel posts as a separate source type.
- Keep comment lineage in metadata for later filtering/dedup.

### RM-012 Career site source v1
- Implement one simple career-site adapter with `httpx + selectolax`.
- Normalize HTML-derived items into `RawItem`.

### RM-013 Real sample fixtures
- Store sanitized real-world fixtures for channels, groups, comments, and career pages.
- Use them in regression tests for source adapters.

## Phase 2. Input hygiene and protection

Purpose: defend the pipeline from noisy or malformed raw input before expensive work.

### RM-014 SanitizeNode v1
- Normalize whitespace, control characters, broken encoding, and URL formatting.
- Guarantee that `SanitizeNode` is first in every pipeline chain.

### RM-015 Raw input validation
- Reject malformed `RawItem` objects and missing required fields.
- Add explicit rejection reasons.

### RM-016 URL and origin policy
- Validate external URLs and allowed career-site domains.
- Reject suspicious or malformed origins.

### RM-017 Quarantine flow
- Route malformed or suspicious raw items to a quarantine sink/log.
- Keep them observable instead of silently dropping them.

## Phase 3. Early signal shaping

Purpose: cheaply separate likely-job signal from obvious noise.

### RM-018 Heuristic triage node
- Drop empty, too-short, and obviously irrelevant posts before extraction.
- Add stable rejection reason taxonomy.

### RM-019 Telegram-specific heuristics
- Add rules for vacancy-like Telegram messages.
- Tune for channels, groups, and comments separately.

### RM-020 Career-site heuristics
- Drop navigation pages, generic company pages, and non-job content.

### RM-021 Stage conversion reporting
- Report conversion across raw -> sanitized -> triaged.
- Make source quality visible by source type.

## Phase 4. Dedup and identity

Purpose: stabilize reruns and avoid inflated job counts.

### RM-022 Raw item identity
- Define processed keys per source/external ID/URL.
- Make reruns idempotent at raw-item level.

### RM-023 Job dedup v1
- Dedup by canonical URL and normalized text/title/company signals.

### RM-024 Near-duplicate detection
- Add fuzzy dedup with `rapidfuzz` for reposts and edited duplicates.

### RM-025 Cross-source dedup
- Detect duplicates across Telegram and career-site sources.

### RM-026 Dedup explainability
- Persist why an item/job was marked as duplicate.

## Phase 5. Extraction and schema quality

Purpose: turn filtered raw signal into structured jobs.

### RM-027 Job schema v1 finalization
- Finalize the practical MVP `Job` schema.
- Distinguish required vs optional fields.

### RM-028 Extraction node v1
- Convert `RawItem` into structured `Job` output.
- Handle parse success, partial success, and failure explicitly.

### RM-029 LLM provider adapter
- Implement `openai + instructor` behind `LLMProvider`.
- Add timeout, retry, and schema validation behavior.

### RM-030 Extraction validation layer
- Validate extracted jobs after parsing.
- Reject or downgrade low-quality results.

### RM-031 Partial extraction strategy
- Preserve useful partial jobs instead of losing the full item.

### RM-032 Gold sample evaluation set
- Maintain a real-world extraction sample set for regression and manual scoring.

## Phase 6. Job quality and relevance

Purpose: make output useful for the target AI-jobs niche.

### RM-033 Job validation node
- Enforce minimum viable usefulness for emitted jobs.

### RM-034 AI-role relevance filter
- Keep jobs in the target niche: LLM, AI PM, MLOps, AgentOps, AI Infra, related roles.

### RM-035 Title and company normalization
- Clean noisy company/title strings after extraction.

### RM-036 Location and work-mode normalization
- Normalize remote, hybrid, on-site, and free-form location text.

### RM-037 Compensation parsing v1
- Parse salary and compensation details when present.

### RM-038 Quality scoring
- Add a score or confidence indicator for downstream use and review.

## Phase 7. Outputs and feedback loop

Purpose: make results consumable by operators and downstream users.

### RM-039 JSON sink hardening
- Support atomic writes, stable schema versioning, and JSONL mode.

### RM-040 Rejected-items sink
- Write dropped, quarantined, and failed items to a separate sink.

### RM-041 Posting sink v1
- Publish selected jobs to Telegram or another outbound target.

### RM-042 Human review output
- Export borderline jobs for quick manual review.

### RM-043 CLI run modes
- Add `once`, source selection, output path, item limit, and dry-run modes.

### RM-044 Run summary report
- Summarize per-source counts, duplicates, failures, extracted jobs, and posted jobs.

## Phase 8. Reliability and failure isolation

Purpose: survive bad items and partial infrastructure failures.

### RM-045 Per-item fault isolation
- Keep one bad item from failing the whole run.

### RM-046 Source retry and timeout policy
- Add retry/timeout rules for Telegram and HTTP sources.

### RM-047 LLM resilience rules
- Add bounded retries, timeout control, and safe failure behavior for LLM extraction.

### RM-048 Sink recovery behavior
- Define behavior for file-write and posting failures.

### RM-049 Resume and rerun semantics
- Make reruns predictable using store state.

### RM-050 Operational guards
- Add max items, max text length, and source-specific resource limits.

## Phase 9. Test and regression system

Purpose: keep velocity without breaking pipeline quality.

### RM-051 Domain and node unit test pack
- Cover core nodes and domain invariants thoroughly.

### RM-052 Pipeline slice integration tests
- Add end-to-end tests for channels, groups/comments, and career-site slices.

### RM-053 Real-world regression fixtures
- Preserve representative source inputs and expected outcomes.

### RM-054 Dedup regression pack
- Lock down hard duplicate and near-duplicate cases.

### RM-055 Extraction evaluation harness
- Add a repeatable offline quality check against the gold sample set.

### RM-056 Port contract tests
- Ensure new source/node/sink adapters respect shared contracts.

## Phase 10. MVP release contour

Purpose: make the project usable by someone other than the author.

### RM-057 Env examples and config docs
- Provide clean `.env` examples and required variable documentation.

### RM-058 Runnable README flow
- Document setup from clone to first emitted JSON jobs.

### RM-059 Sample outputs and examples
- Add sample job output and source configuration examples.

### RM-060 Source setup guides
- Document Telegram channel/group/comment setup and career-site configuration.

### RM-061 Troubleshooting guide
- Document common Telegram auth, parsing, and extraction failure modes.

### RM-062 Release checklist
- Define the final MVP verification flow and publication checklist.

## Parallel work streams after Phase 0

### Stream A. Telegram
- Channels, groups, comments, Telegram-specific heuristics, Telegram posting.

### Stream B. Career sites
- Career-site adapters, HTML normalization, source-specific heuristics.

### Stream C. Cross-cutting quality
- Sanitize, validation, dedup, extraction, scoring, sinks, resilience, tests.

## Suggested milestone boundaries

- `M1 - Spine`
- `M2 - Real-world ingestion`
- `M3 - Input hygiene and triage`
- `M4 - Dedup and identity`
- `M5 - Extraction`
- `M6 - Job quality`
- `M7 - Outputs and feedback`
- `M8 - Reliability`
- `M9 - Test and regression`
- `M10 - MVP release contour`
