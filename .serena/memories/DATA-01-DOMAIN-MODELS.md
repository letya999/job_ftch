<!-- Memory Metadata
Last updated: 2026-06-06
Last commit: 35f604c feat(persistence): add PostgreSQL store backend
Scope: domain/, tests/test_domain_models.py, tests/test_dedup.py, tests/test_store.py
Area: DATA
-->

# DATA-01-DOMAIN-MODELS

## Purpose

Document domain model contracts and identity data used by pipeline nodes and
store adapters.

## Source Of Truth

- `domain/models.py`: `SourceKind`, `WorkMode`, `CompensationRange`, `RawItem`,
  and `Job`.
- `domain/quarantine.py`: raw rejection reasons and `QuarantinedRawItem`.
- `domain/dedup.py`: dedup key and duplicate explanation models.
- `domain/triage.py`: triage signal models.
- `domain/__init__.py`: public domain exports.

## Entry Points

- `RawItem.model_validate(...)`: validates source records and computes stable IDs.
- `Job.model_validate(...)`: validates extracted job candidates.
- `processed_key_for_raw_item(...)`: builds processed-state keys.

## Current Behavior

Domain models are Pydantic v2 models with strict extra-field behavior. Stable
IDs are deterministic and belong to domain model validation, not infrastructure
adapters.

`QuarantinedRawItem` captures source or node rejections without requiring
downstream code to handle raw invalid payloads.

## Contracts And Data

- `RawItem` includes source kind/name, text, optional URL, optional external ID,
  timestamps, metadata, and stable ID.
- `Job` is the future structured output model; current runtime still emits
  `RawItem`.
- Dedup data uses typed `RememberedDedupKey` and `DuplicateRecord` payloads.

## Invariants

- `domain/` imports only stdlib and `pydantic`.
- Domain objects must not perform I/O or import application/infrastructure code.
- No secrets or private user identifiers belong in fixtures, docs, or memories.

## Change Rules

- If identity formulas change, update domain tests and docs together.
- Keep new persistence details out of domain models; stores adapt domain payloads.

## Verification

- `uv run pytest tests/test_domain_models.py`: model invariants and validation.
- `uv run pytest tests/test_dedup.py tests/test_store.py`: dedup and store-domain data contracts.
