<!-- Memory Metadata
Last updated: 2026-06-06
Last commit: 074ae27 feat(pipeline): add PostgreSQL-backed foundation
Scope: application/contracts.py, application/context.py, application/outcomes.py, application/pipeline.py, application/pipeline_handlers.py, application/run_summary.py, application/rejections.py, nodes/sanitize.py, nodes/validate_raw.py, nodes/origin_policy.py, tests/test_pipeline.py, tests/test_input_hygiene.py, tests/test_validate_raw.py, tests/test_origin_policy.py, tests/test_outcomes.py, tests/test_run_summary.py, tests/test_app_e2e.py
Area: BACKEND
-->

# BACKEND-01-PIPELINE-QUARANTINE

## Purpose

Document pipeline orchestration, first-node sanitation, raw validation, origin policy, and quarantine behavior.

## Source Of Truth

- `application/contracts.py`: `Source`, `Node`, `Sink`, `Store`, `LLMProvider`.
- `application/context.py`: `ProcessingContext`.
- `application/outcomes.py`: `NodeOutcome`, `OutcomeKind`, `PipelineStage`, `RejectReason`.
- `application/pipeline.py`: `Pipeline`.
- `application/pipeline_handlers.py`: outcome, quarantine, source failure, sink failure, and sink finalization handlers used by `Pipeline`.
- `application/run_summary.py`: `RunSummary`.
- `application/rejections.py`: `RawItemRejected`.
- `nodes/sanitize.py`: `SanitizeNode` for sanitation and URL-shape normalization.
- `nodes/validate_raw.py`: `ValidateRawNode` for raw usefulness and locator limits.
- `nodes/origin_policy.py`: `OriginPolicyNode` for Telegram/career-site URL origin policy.
- `tests/test_pipeline.py`: happy path, drop semantics, first-node invariant, local CLI smoke.
- `tests/test_input_hygiene.py`: sanitizer and quarantine regression tests.
- `tests/test_validate_raw.py`: raw validation node behavior.
- `tests/test_origin_policy.py`: URL/origin policy behavior.
- `tests/test_app_e2e.py`: positive and negative multisource e2e fixtures plus context propagation.

## Entry Points

- `Pipeline.__init__`: validates that node chain is non-empty and first node has `is_sanitize`.
- `Pipeline.run(max_items=None, context=None)`: consumes source items, applies nodes with `ProcessingContext`, emits sink output, marks processed IDs, routes quarantine, finalizes sinks, and returns `RunSummary`.
- `SanitizeNode.process(item, context)`: normalizes raw items and returns `NodeOutcome[RawItem]`.
- `ValidateRawNode.process(item, context)`: quarantines empty source/text, text above `context.max_text_length`, and items without `external_id` or `url`.
- `OriginPolicyNode.process(item, context)`: quarantines invalid/disallowed/private URL hosts for source URLs and metadata origin URLs.

## Current Behavior

`Pipeline.run` tracks `fetched`, `source_records`, `sanitized`, `dropped`, `emitted`, `quarantined`, `failed`, `extracted`, `duplicates`, plus per-stage, per-reason, and per-source counters in `RunSummary`. Nodes return `NodeOutcome` values for pass/drop/quarantine/fail. Source-level `QuarantinedRawItem` records bypass nodes and go directly to the quarantine sink. Source iteration failures increment `failed` and are emitted as `source_fetch_error` quarantine records when a quarantine sink exists.

`Pipeline` checks/marks processed IDs at the pre-emit boundary using the current item after node processing. This preserves sanitized `stable_id` recomputation when URL/source fields change. `Pipeline.run` accepts an optional `ProcessingContext`; `app.py` builds one from runtime settings.

`SanitizeNode` owns only sanitation and URL-shape normalization: Unicode normalization, control-character removal, whitespace normalization, URL scheme/netloc lowercasing, fragment stripping, and validation-safe `RawItem.model_validate(...)` reconstruction. Host allowlist checks live in `OriginPolicyNode`.

`ValidateRawNode` enforces raw record usefulness and max text length. `OriginPolicyNode` enforces allowed URL origins, including Telegram `t.me` host policy and career-site allowlist/private-host checks.

## Contracts And Data

Allowed Telegram URL hosts are `t.me` and `www.t.me`. Career-site URL hosts must be configured through `Settings.career_site_allowed_hosts` or passed to `OriginPolicyNode`. Metadata URL fields validated by `OriginPolicyNode` are `board_url`, `job_url`, and `post_url`.

`Pipeline` emits the current item to the primary sink only after all nodes pass. It calls `Store.mark_processed(stable_id)` after successful emission. It calls `finalize()` on both primary and quarantine sinks at the end of each run.

## Invariants

- `SanitizeNode.is_sanitize` must remain truthy.
- `SanitizeNode` must stay first in `app.py:build_nodes`.
- Rejected malformed input must be observable through quarantine rather than silently lost.
- Source fetch failures must not be swallowed.
- Node exceptions must be counted as failures and must not stop later source records from processing.
- Sink emit failures must not mark an item as processed.
- Origin/host policy belongs in `OriginPolicyNode`, not in `SanitizeNode`.

## Change Rules

- Any node-chain change must preserve the first sanitizer invariant and update `tests/test_pipeline.py` plus affected e2e tests.
- Any sanitizer policy change must update `tests/test_input_hygiene.py`.
- Any raw validation or origin policy change must update `tests/test_validate_raw.py` or `tests/test_origin_policy.py`.
- Keep quarantine payloads structured; do not encode rejection facts only in logs.
- New nodes must expose `name`, `stage`, and return `NodeOutcome`.

## Verification

- `uv run pytest tests/test_pipeline.py`: pipeline orchestration, drop semantics, ordering invariant.
- `uv run pytest tests/test_input_hygiene.py`: sanitizer and quarantine behavior.
- `uv run pytest tests/test_validate_raw.py tests/test_origin_policy.py`: raw protection node behavior.
- `uv run pytest tests/test_outcomes.py tests/test_run_summary.py`: outcome and summary contracts.
- `uv run pytest tests/test_app_e2e.py`: end-to-end positive and quarantine flows.
