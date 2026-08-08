---
title: "080 — Opt-in operational outcomes store (REVIEW / REJECTED)"
description: "**Status**: ACCEPTED"
updated: 2026-08-08
---
# 080 — Opt-in operational outcomes store (REVIEW / REJECTED)

**Status**: ACCEPTED  
**Date**: 2026-08-08  
**Related**: ADR-009, ADR-018, ADR-052, ADR-079

## Context

`search_jobs` and the job catalog only contain ACCEPT groups. REVIEW and REJECTED
lanes historically wrote optional jsonl dumps (or nothing when `sink_backend=none`).
Rejected dumps used full `RejectedItem.snapshot` payloads and bloated artifacts.
MCP and ops need to inspect REVIEW/REJECTED after a run without polluting the
catalog or always writing fat files.

## Decision

1. **Catalog stays ACCEPT-only** (`JobGroupStore` / `search_jobs`).
2. **Operational lanes** REVIEW and REJECTED may persist **compact** rows into the
   tenant store when explicitly enabled.
3. Enablement is per-lane via `review_output.backend` / `rejected_output.backend`
   (Settings: `review_output_backend`, `rejected_output_backend`):

   | value | file | store |
   |-------|------|-------|
   | `null` (default) | inherit main `sink_backend` | off |
   | `none` | off | off |
   | `json_file` (or other sink) | on | off |
   | `store` | off | on |
   | `both` | jsonl | on |

4. Default remains **store off** so bot/prod with `sink_backend=none` is unchanged.
5. MCP local profile sets `review_output.backend: store` and
   `rejected_output.backend: store`.
6. Projections: `compact_review_payload` / `compact_rejected_payload` (no full
   snapshot in the default path).
7. Application APIs: `TenantRunner.list_review_jobs` / `list_rejected`; MCP tools
   of the same names. When store is disabled, tools return
   `{enabled: false, items: []}`.

## Consequences

- (+) MCP can triage non-ACCEPT without reading disk dumps.
- (+) Bot and eval keep existing defaults.
- (+) Disk bloat from full rejected snapshots is removed from the default sink path.
- (-) Call sites that need full rejected snapshots must use an explicit debug export.
