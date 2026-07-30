---
title: "063 — Controlled evidence fan-out and deferred resolution"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 063 — Controlled evidence fan-out and deferred resolution

**Status**: ACCEPTED
**Date**: 2026-07-10

## Decision

The common pipeline remains sequential at identity and side-effect boundaries.
Independent evidence producers run inside one typed `EvidenceFanOutNode` with
bounded concurrency, item deadlines and existing budgets. A single
`DecisionNode` owns routing decisions.

If a critical claim is unknown, expensive or temporarily unavailable, the
candidate becomes deferred and enters a durable resolver queue. Provider,
timeout and budget failures never become relevance rejects.

## Consequences

- Latency improves for independent local signals.
- Recall is protected from transient provider failures.
- A new workflow engine is not required.
