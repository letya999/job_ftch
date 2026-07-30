---
title: "031 — Run-Based Source Snapshot (TD-013)"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 031 — Run-Based Source Snapshot (TD-013)

**Status**: ACCEPTED
**Date**: 2026-06-18
**Closes**: TD-013 in `docs/techdebt.md`

## Context

`SnapshotFilterNode` (`job_ftch/nodes/snapshot_filter.py:30-74`) is wired into
every source between `SanitizeNode` and the rest of the pipeline
(`tenant_runner.py:860`). It drops a `RawItem` only when the current
`sha256(url|text|source_name)` matches the previous run's hash **byte-for-byte**.

Concretely, the existing logic in `snapshot_filter.py:50-58`:

```python
new_hash = _content_hash(item)
previous_hash = snapshot.get(item_id)
if previous_hash == new_hash:
    raise RawItemDropped(...)
```

means: if a career-site vacancy changed the salary line since yesterday, the
item passes through and gets re-emitted in Telegram. The filter is therefore a
**byte-equality change-detection** aid that saves LLM calls, not a freshness
gate. It is also a **last-write-wins blob** stored in `jf_kv` under
`snapshot:{tenant_id}:{source_id}:latest` (rewritten whole on every save via
`save_source_snapshot` in `tenant_runner.py:284-289`).

The migration `002_source_snapshots.sql` (and `_pg` variant) has been on disk
since before TD-013 was filed but no code path writes to it. The table is
indexed on `(tenant_id, source_id, stable_id, run_at DESC)` and the schema is
ready for a true per-run history.

Ingest sources are heterogeneous (`source_spec.py:12-152`): each
`BaseSourceSpec` may declare `interval_seconds` from seconds to days, and there
are also `WebhookSourceSpec`, `WebSocketSourceSpec`, `EventListenerMode`, and
`TelegramRealtimeSourceSpec`. A fixed `seen_within_hours` window does not adapt
to this — polling every 20 minutes and webhook pushes have very different
"already shown" semantics. The right primitive is "was this `stable_id` in
the last run of this source?", not "was it in the last N hours?".

## Decision

Adopt a run-based snapshot model. The contract:

1. **Snapshot table = `jf_source_snapshots`** with the existing schema
   (`tenant_id`, `source_id`, `run_id`, `run_at`, `stable_id`, `item_hash`,
   `item_json`). Index `(tenant_id, source_id, stable_id, run_at DESC)` makes
   "last run for this source" a single-indexed range scan.
2. **Drop rule** — a `RawItem` is dropped if its `stable_id` appears in the
   **most recent run** (`ORDER BY run_at DESC LIMIT 1`) of its `source_id`.
   The semantic is "if the previous run of this source already saw this item,
   do not process it again."
3. **`run_id` source** — caller supplies the id (e.g. a UUID generated per
   pipeline run in `TenantRunner` or per push event in webhook/realtime
   modes). The store does not invent run ids.
4. **TTL purge** — rows newer than `ttl_days` (default 7) are deleted at the
   end of every `save_snapshot_rows` call. Auto-purge, no separate scheduler
   job. Rationale: if a source is silent for 7 days, the snapshot for its
   last run is stale and any item seen today should be processed.
5. **Two-factor dedup preserved** — `SnapshotFilterNode` and `DedupNode`
   stay separate:
   - `SnapshotFilterNode` — per-source, per-run, exact-`stable_id`. Cheap.
   - `DedupNode` — cross-source, forever, URL/content/near-match (rapidfuzz).
6. **New `Store` protocol methods** — three additions, all backend-portable:
   - `get_last_run_snapshot(source_id) -> frozenset[str]`
   - `save_snapshot_rows(source_id, run_id, rows) -> None`
   - `purge_old_snapshots(*, older_than_days) -> int`

## Consequences

- (+) Snapshot is row-level, no whole-blob rewrites; concurrent writes
  no longer race on the same `jf_kv` key.
- (+) Historical diff is one SQL: "stable_ids in run X but not in run Y".
- (+) Run-based semantics adapt to every ingest cadence without per-source
  override.
- (+) `DedupNode` semantics unchanged; existing dedup keys still persist
  forever.
- (-) One extra table to keep in sync; auto-purge needs careful deployment
  to avoid starving slow-cadence sources (7-day TTL is a deliberate safety
  margin).
- (-) Backwards-incompatible: existing `load_source_snapshot` /
  `save_source_snapshot` on `TenantStore` remain for legacy callers, but
  the new `SnapshotFilterNode` reads from the table, not the blob. Old
  blobs in `jf_kv` are left to TTL out naturally.
- (-) Per-item `item_json` column is stored but unused by this change; it
  is the seed for future delisted detection (TD-014).
