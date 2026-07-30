---
title: "026 — Runtime source overlay in tenant store"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 026 — Runtime source overlay in tenant store

**Status**: ACCEPTED
**Date**: 2026-06-13

## Context

Tenants are currently defined by static YAML/JSON config files. That is good for bootstrapping, but bad for the operating mode we now want:

- add one more Telegram/chat/site source from bot or API without editing files;
- persist the change across restarts;
- keep the core library-first and store-agnostic;
- avoid a second config system parallel to `TenantConfig`.

The existing `StoreConnector` contract already gives us tenant-scoped durable key/value + set primitives across memory, SQLite, and PostgreSQL backends.

## Decision

Treat runtime-managed sources as a tenant-scoped overlay stored in the tenant store.

- Static config files remain the base source list.
- Runtime-added sources are persisted as `RuntimeSourceRecord` payloads in the tenant store.
- Disabled sources are also stored in the tenant store as an overlay, so both config-backed and runtime-backed sources can be paused without editing YAML.
- `TenantRunner` hydrates the overlay lazily and rebuilds the effective source list as:

`effective_sources = base_sources + enabled_runtime_sources - disabled_source_ids`

- User-facing adapters (Telegram bot, FastAPI bridge, MCP) resolve a simple link/input into a `SourceSpec`, then call runner methods to persist and activate it.

## Consequences

- (+) Sources can be added and disabled at runtime without mutating committed config files.
- (+) Overlay persistence works across `memory`/`sqlite`/`postgres` because it only depends on `StoreConnector`.
- (+) `TenantConfig` stays small; we do not introduce a second heavyweight registry subsystem.
- (+) Static tenant config remains the bootstrap source of truth, while runtime ops stay reversible.
- (-) Effective tenant config is no longer equal to file contents alone; operators must inspect runtime source state too.
- (-) Overlay hydration is async, so `TenantRunner` must load it lazily before source-sensitive operations.
