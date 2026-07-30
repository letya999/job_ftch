---
title: "005 - Raw Item Identity And Dedup"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 005 - Raw Item Identity And Dedup

**Status**: ACCEPTED
**Date**: 2026-06-06

## Context
Phase 4 requires raw-item idempotency across reruns, duplicate detection before extraction, cross-source duplicate detection, and persisted explainability for duplicate decisions. The existing pipeline only remembered emitted item IDs and had no durable explanation for why a later item was dropped as a duplicate.

## Decision
Introduce a raw-item dedup strategy centered on `RawItem`:
- Compute a dedicated processed key from `source_kind + source_name + external_id/url` and mark it after every terminal outcome, not only after emit.
- Add a `DedupNode` after `HeuristicTriageNode` and before the sink.
- Detect duplicates in three passes:
  - exact canonical job URL match;
  - exact normalized content match from title/company/text signals;
  - near-duplicate match with `rapidfuzz` over normalized fingerprints.
- Extend `Store` to persist remembered dedup keys and explicit duplicate decision records.

## Consequences
- (+) Reruns become idempotent at raw-item level even for duplicates and other dropped items.
- (+) Telegram and career-site items can deduplicate against each other before extraction exists.
- (+) Duplicate decisions are explainable and testable via persisted records.
- (-) `InMemoryStore` now carries more state and the `Store` protocol is richer, which future backends must implement.
