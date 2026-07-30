---
title: "010 - Reliability And Recovery Policies"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 010 - Reliability And Recovery Policies

**Status**: ACCEPTED
**Date**: 2026-06-06

## Context
Phases 8-10 require the pipeline to survive malformed items, transient source failures, and secondary output failures without losing the core processing path. The existing implementation had three gaps:
- unexpected node exceptions could abort the whole run;
- source timeout and retry behavior was implicit or scattered across adapters;
- JSON file output was only finalized at flush time, so a late write failure weakened rerun predictability.

## Decision
- Treat unexpected per-item processing errors as isolated failures: record them in the rejected flow and continue with the next item.
- Keep source retry and timeout policy adapter-local:
  - Telethon uses explicit bounded client retry settings from `Settings`;
  - career-site HTTP fetches use explicit timeout, connection limits, and bounded async retries.
- Add operational guards in the sanitize-first boundary, including a max text length check.
- Make `JsonFileSink` stage payloads durably before finalization so a failed flush can be recovered on the next run.
- Wrap non-primary sinks (review, rejected, quarantine, posting) in a failure-tolerant adapter so secondary delivery issues do not abort the main run.
- Persist lightweight run-state markers in `Store` (`running` / `completed` / `completed_with_failures` / `failed`) plus last processed identity for predictable reruns.

## Consequences
- (+) One bad item no longer fails the whole batch.
- (+) Retry and timeout behavior becomes explicit and testable.
- (+) File outputs can recover from failed finalization without re-fetching the source.
- (+) Operators can inspect the last run status through the store contract.
- (-) Output durability semantics are more complex because sinks now maintain staging state.
