---
title: "052 — Immutable observation ledger and content-versioned replay"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 052 — Immutable observation ledger and content-versioned replay

**Status**: ACCEPTED
**Date**: 2026-07-10
**Supersedes/Transitions**: ADR-005, ADR-031 and ADR-036 retain their historical behaviour until the ledger is wired through every persistent store and pipeline path.

## Context

The current `RawItem.stable_id` identifies a source locator. Treating it as a
processed identity suppresses changed content under the same URL or Telegram
message id. A replay also needs to distinguish a new observation from a new
decision policy applied to existing content.

## Decision

An immutable observation is identified by `observation_id` and stores the raw
`RawItem` JSON, a SHA-256 content hash, source cursor/context identifiers, and
the version numbers used for content and policy decisions. Content identity is
`(stable_id, content_hash)`; a policy re-score changes only `decision_version`.
The domain representation is storage-agnostic and contains no infrastructure
imports. Persistent ledger storage and pipeline wiring are follow-up work in
Phase 1.1 and must not silently fall back to `stable_id`-only semantics.

## Consequences

- Changed content at one locator can be replayed and re-decided.
- Raw payload provenance becomes explicit rather than inferred from mutable
  snapshots.
- Existing processed-key suppression remains transitional until all stores and
  pipeline paths use the ledger.
