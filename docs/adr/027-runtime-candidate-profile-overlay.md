---
title: "027 — Runtime candidate profile overlay in tenant store"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 027 — Runtime candidate profile overlay in tenant store

**Status**: ACCEPTED
**Date**: 2026-06-13

## Context

The matching pipeline already computes profile-aware relevance for `JobRecord`, but profile catalogs are currently static and file-backed. That does not support the operating mode we want:

- one Telegram user keeps multiple runtime profiles;
- the user can switch the active profile without editing files;
- search and digest should rerank against the active user profile immediately;
- the solution must stay store-agnostic and reuse the tenant-scoped runtime model already used for source overlays.

## Decision

Persist candidate profiles as tenant-scoped runtime records in the tenant store.

- Candidate profiles are stored per `(user_id, profile_id)` as `ManagedCandidateProfile`.
- Active profile is stored separately per `user_id`.
- `TenantRunner` exposes save/list/set-active/get-active operations.
- `TenantRunner.search_jobs()` and `TenantRunner.latest_jobs()` accept optional `user_id` / `profile_id` and rerank the already-fetched `JobGroup` list in memory using the existing `MultiProfileMatchNode`.
- Adapter surfaces (Telegram bot, API bridge, MCP) write and read these runtime profiles; static file-backed catalogs remain the bootstrap/default profile path.

## Consequences

- (+) Profile operations work across memory / SQLite / PostgreSQL because they only depend on `StoreConnector`.
- (+) User-facing relevance changes take effect immediately without rebuilding tenant config.
- (+) Existing matching logic is reused; no second scoring engine is introduced.
- (-) Runtime user profiles are not part of the static tenant config dump.
- (-) Initial bot profile capture is intentionally simple text-first input, not full resume file parsing.
