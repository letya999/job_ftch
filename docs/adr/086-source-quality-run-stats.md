---
title: "086 — Human source labels and per-run ingest stats"
description: "**Status**: ACCEPTED"
updated: 2026-09-04
---
# 086 — Human source labels and per-run ingest stats

**Status**: ACCEPTED
**Date**: 2026-09-04
**Extends**: [069-split-operational-and-ml-observability.md](069-split-operational-and-ml-observability.md)

## Context

Rolling-window labels `reliable` / `rich` / `high_relevance` describe how a
source behaved over the last 20 pipeline runs. `important` was briefly derived
from the same window. Operators instead need to **pin** boards they care about
even while those boards are currently failing.

Run history already lives as JSON blobs in `jf_kv` (`run_history:*`). That is
enough to recompute window labels, but it is a poor place to chart conversion,
latency, cost, or "what happened to HH in run X". OpenObserve already receives
end-of-run counters and histograms, yet there is no first-class per-run /
per-source event and no shipped dashboard, so the UI cannot answer:

- how source statuses moved across runs;
- which sources are currently important / reliable / rich / high-relevance;
- conversion, wall latency, and LLM cost for one run and over time.

ADR-069 still holds: PostgreSQL is the authority for business run history;
OpenObserve is the diagnostic view; Langfuse stays the ML/LLM trace store.

## Decision

1. **`important` is operator-set.** It is not classified. Canonical key is the
   same collapsed source key as quality labels (`hirehi_ru_kw3` → `hirehi_ru`).
   Operators set it through MCP `set_source_important` or
   `POST /pipeline/sources/{tenant}/important`. No hardcoded board list.

2. **Postgres/SQLite tables are the authority.**

   | Table | Grain | Role |
   |---|---|---|
   | `jf_source_operator_flags` | tenant + source_key | human `important` |
   | `jf_pipeline_run_stats` | tenant + source_run_id | funnel, conversion, duration, LLM cost |
   | `jf_source_run_stats` | tenant + run + source_id | status, yield, emitted, labels as of that run |

   KV `run_history:*` and `SourceHealth` remain. The new tables are a relational
   projection written at the end of each real pipeline run (not lock-skip
   probes). Window labels `reliable` / `rich` / `high_relevance` stay computed
   and are snapshotted onto `jf_source_run_stats` so history does not depend on
   the latest `SourceHealth` blob.

3. **OpenObserve consumes the same payload as logs, not as latest-only gauges.**
   After each run the process emits `pipeline_run_stats` (one line) and
   `source_run_stats` (one line per source) with searchable fields: counts,
   conversion, `duration_ms`, `llm_cost_usd`, `llm_latency_ms`, status, and the
   four quality flags. Existing ingest counters/histograms stay. Latest-state
   gauges on `SourceHealth` remain for "who is tagged right now".

4. **Dashboards ship as JSON** under `deploy/observability/dashboards/` and are
   upserted best-effort when OpenObserve is configured. Tabs:

   - runs over time (latency, cost, conversion, source ok/fail);
   - one run (filter `source_run_id`);
   - four tables: important / reliable / rich / high-relevance.

   SQL against the logs stream is the dashboard source of truth so a specific
   run is filterable. Langfuse is not duplicated.

5. Labels still do **not** change fetch order, retries, or accept/reject.
   `watch_source_ids` is the human important set.

## Consequences

- (+) Operators pin key boards without waiting for the 20-run window.
- (+) Postgres can answer "conversion of the last N runs" and "status of this
  source across runs" without parsing KV JSON.
- (+) OpenObserve can chart latency/cost/conversion and list tagged sources.
- (-) Dual-write: a failed stats insert must not fail the pipeline; it is
  logged. Recompute from `run_history` is possible but not automatic.
- (-) Per-source wall time is not yet measured at fetch; run-level duration and
  per-source LLM latency/cost are what the first dashboards show.
