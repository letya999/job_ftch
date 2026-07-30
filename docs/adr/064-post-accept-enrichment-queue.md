---
title: "ADR-064: Post-accept enrichment queue"
description: "ACCEPTED"
updated: 2026-07-24
---
# ADR-064: Post-accept enrichment queue

## Status

ACCEPTED

## Decision

Full extraction, translation, presentation, embedding and company enrichment are
represented as idempotent store-backed `EnrichmentTask` records after ACCEPT. They are
not stages in the terminal policy graph and cannot change the terminal decision.

## Rationale

These operations are useful but expensive and non-critical to deciding whether a
vacancy is relevant. Keeping them after durable delivery preserves fast-path latency,
prevents provider failures from becoming false rejects, and allows retries without
duplicating delivery.
