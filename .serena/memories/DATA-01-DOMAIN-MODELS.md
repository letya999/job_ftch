<!-- Memory Metadata
Last updated: 2026-06-06
Last commit: 074ae27 feat(pipeline): add PostgreSQL-backed foundation
Scope: domain/models.py, domain/quarantine.py, application/outcomes.py, application/rejections.py, tests/test_domain_models.py, tests/test_input_hygiene.py, tests/test_validate_raw.py, tests/test_origin_policy.py
Area: DATA
-->

# DATA-01-DOMAIN-MODELS

## Purpose

Document domain data contracts, stable IDs, and quarantine/rejection payloads.

## Source Of Truth

- `domain/models.py`: `RawItem`, `Job`, `CompensationRange`, `SourceKind`, `WorkMode`.
- `domain/quarantine.py`: `RawItemRejectionReason`, `QuarantinedRawItem`.
- `application/outcomes.py`: application-level `RejectReason` taxonomy used by nodes.
- `application/rejections.py`: `RawItemRejected` and conversion to quarantine records.
- `tests/test_domain_models.py`: domain invariants and serialization expectations.
- `tests/test_input_hygiene.py`: sanitizer and quarantine behavior around malformed data.
- `tests/test_validate_raw.py`: raw validation rejection reasons.
- `tests/test_origin_policy.py`: origin policy rejection reasons.

## Entry Points

- `RawItem.model_validate(...)`: validates source adapter output.
- `Job.model_validate(...)`: target structured vacancy schema for later extraction.
- `RawItemRejected.to_quarantined()`: converts node-level rejections into `QuarantinedRawItem`.

## Current Behavior

`RawItem` is the active pipeline item. It is frozen, forbids extra fields, trims key string fields, requires non-blank `source_name` and `text`, and requires either `external_id` or `url`. `stable_id` is a SHA-256 hash over normalized `source_kind`, `source_name`, `external_id`, and `url`.

`Job` exists as a domain model but is not emitted by the current pipeline. Its `stable_id` is a SHA-256 hash over canonical URL, title, company, and raw item ID.

`QuarantinedRawItem` records rejected source payloads, sanitizer rejections, raw validation rejections, origin policy rejections, and source fetch failures with reason, details, optional source locator fields, quarantine timestamp, and snapshot.

## Contracts And Data

`RawItem` fields:

- `stable_id: str`
- `source_kind: SourceKind`
- `source_name: str`
- `external_id: str | None`
- `url: AnyHttpUrl | None`
- `text: str`
- `fetched_at: datetime`
- `created_at: datetime | None`
- `metadata: dict[str, Any]`

`Job` fields:

- `stable_id`, `raw_item_id`, `source_kind`, `source_name`, `title`, `company`, `description`
- optional `canonical_url`, `location`, `work_mode`, `compensation`, `metadata`

`RawItemRejectionReason` values are `empty_text`, `empty_source_name`, `missing_locator`, `text_too_long`, `invalid_url`, `invalid_origin_url`, `disallowed_url_host`, `disallowed_origin_host`, `private_url_host`, `private_origin_host`, `invalid_raw_item`, and `source_fetch_error`.

Application `RejectReason` mirrors the raw protection reasons needed by nodes and also includes roadmap reasons for dedup, extraction, quality, and relevance.

## Invariants

- Domain models are pure and must not perform I/O.
- Domain models use `ConfigDict(extra="forbid", frozen=True)`.
- `CompensationRange` requires at least one bound and `min_amount <= max_amount` when both bounds exist.
- Rejection snapshots must not store secrets.

## Change Rules

- Add domain fields only with tests for validation, serialization, and stable ID impact.
- Keep source-specific raw metadata in `RawItem.metadata`; do not add adapter-specific fields to `RawItem` unless they are canonical across sources.
- If changing rejection reasons, update sanitizer, raw validation, origin policy, pipeline quarantine tests, and e2e negative fixture expectations.

## Verification

- `uv run pytest tests/test_domain_models.py`: verifies domain invariants.
- `uv run pytest tests/test_input_hygiene.py`: verifies sanitizer-to-quarantine contracts.
- `uv run pytest tests/test_validate_raw.py tests/test_origin_policy.py`: verifies raw protection rejection reasons.
- `uv run mypy domain application`: verifies domain/application type contracts.
