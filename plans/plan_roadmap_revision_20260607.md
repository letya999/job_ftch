# Plan: Roadmap revision — fix 4 issues from architecture review

Date: 2026-06-07
Target file: `docs/roadmap.md` (single file, edit in place)
Scope: ONLY edit `docs/roadmap.md`. No code changes. No other files.

## Context

The roadmap (RM-001..RM-144, 26 phases) is strong. This revision fixes 4 concrete
issues found in review. Apply ALL FOUR. Preserve all existing content and formatting
style (`##` phases, `###` RM tasks, dash bullets) except where explicitly changed below.

## CRITICAL EXECUTION RULES
- This is a documentation edit. Do NOT touch any `.py` files.
- Keep all DONE markers (`— DONE`) exactly as they are on phases 0-10 and 4.5.
- Keep RM IDs stable where possible; only renumber where this plan says so.
- After edits, the file must remain valid Markdown and internally consistent
  (milestone list at the bottom must match the phases above).

---

## FIX 1 — Resolve layering contradiction in RM-138 (dedup vs aggregation)

PROBLEM: RM-138 currently calls `JobIdentityMatcher` INSIDE `DedupNode`, matching on
`title + company_canonical + location`. But `DedupNode` runs in Phase 4 on `RawItem`
(pre-extraction) where those fields do not exist yet — they appear only after
ExtractionNode (Phase 5) and CompanyCanonicalizer (Phase 24). This is a layer violation.

ACTION: Rewrite RM-138 so cross-source identity matching is a POST-extraction stage,
not part of the pre-extraction `DedupNode`. Replace the RM-138 body with:

```
### RM-138 Cross-source identity matching — post-extraction stage
- `JobIdentityMatcher` in `job_ftch/application/identity.py` operates on `Job` objects
  AFTER extraction and company canonicalization, never on pre-extraction `RawItem`.
- New pipeline stage `JobAggregationNode` in `job_ftch/nodes/aggregation.py`, placed
  AFTER `CompanyCanonicalizer` (Phase 24) in the chain. This is the only place merge happens.
- `DedupNode` (Phase 4) is unchanged: it stays RawItem-level (URL + content + near-dup
  by raw text). Its job is "have I seen this raw item before" — drop exact reruns.
- `JobAggregationNode` matching ladder (on `Job`):
  1. Exact canonical_url match.
  2. company_canonical + normalized title + location fingerprint hash.
  3. Optional fuzzy title match (Levenshtein) for slight title variations.
- On match: `JobGroupStore.merge(existing_group, new_job)` instead of dropping.
  On no match: create a new `JobGroup` with this job as the first member.
- Clear separation documented in ADR: DedupNode = RawItem identity (rerun safety);
  JobAggregationNode = cross-source Job identity (one vacancy from many sources).
- Tests: a job that passes DedupNode (new raw item) but matches an existing JobGroup
  is merged, not dropped.
```

Also update RM-137 note and RM-139/RM-140 if they reference DedupNode doing the merge:
- In RM-139, the merge caller is `JobAggregationNode`, not `DedupNode`.
- In RM-140, keep the assertions; ensure they reference `JobAggregationNode`.

---

## FIX 2 — Reorder: canonical-job + lifecycle BEFORE search/persistence

PROBLEM: The domain differentiator (Phase 24 lifecycle/canonicalization, Phase 25
cross-source aggregation) currently lands at M24/M25 — AFTER persistence (M12),
search (M14), MCP (M22), bot (M23). This causes (a) rework: search_jobs/MCP/bot are
built on "drop duplicates" then reworked to "return JobGroup"; (b) the product looks
like "just another scraper" until M24.

ACTION: Move the domain-value layer to run BEFORE the search/persistence/adapter layers.
Concretely, restructure phase ORDER as follows (renumber phase headings, keep RM IDs):

NEW phase order after Phase 13 (Configurable filters):

1. Phase 14 — Domain model hardening  (was Phase 24: RM-134 schema_version,
   RM-135 lifecycle, RM-136 company canonicalization)
2. Phase 15 — Cross-source job aggregation  (was Phase 25: RM-137..RM-140, with FIX 1 applied)
3. Phase 16 — Persistent store  (was Phase 12: RM-068..RM-073) — now JobGroup-aware from
   the start: PostgreSQLJobGroupBackend (RM-139) and PostgreSQLJobBackend designed together.
4. Phase 17 — Fulltext and semantic search layer  (was Phase 14: RM-079..RM-085) —
   search is built group-aware from day one (search_jobs returns JobGroup by default).
   No rework.
5. Phase 18 — Scheduler and daemon mode  (was Phase 15: RM-086..RM-091)
6. Phase 19 — Source config v2: credentials, ingestion modes, bypass (was Phase 16)
7. Phase 20 — Official API sources (was Phase 17)
8. Phase 21 — Browser and hard scraper sources (was Phase 18)
9. Phase 22 — Realtime and push ingestion (was Phase 19)
10. Phase 23 — Library packaging and runtime adapters (was Phase 20: RM-110..RM-114)
11. Phase 24 — Multi-tenant and multi-instance (was Phase 21)
12. Phase 25 — FastMCP server (was Phase 22)
13. Phase 26 — Telegram bot + FastAPI bridge (was Phase 23)
14. Phase 27 — Observability, lineage, unified watermark (was Phase 26: RM-141..RM-144)

IMPORTANT constraints while reordering:
- Keep RM-XXX task IDs unchanged (RM-134 stays RM-134 even though its phase moves up).
  Only the `## Phase N.` heading numbers change. This avoids breaking any external
  references to RM IDs.
- Update every cross-reference inside task bodies that names a phase number
  (e.g. "Phase 12", "Phase 14", "Phase 22 RM-121", "Phase 23 RM-128", "Phase 24",
  "Phase 25", "Phase 26") to the NEW phase number. Search the whole file for
  "Phase 1" through "Phase 26" mentions and fix each to the new numbering.
- Phase 17 (search, formerly 14): update RM-081/RM-085 notes so search returns JobGroup
  by default (group-aware), since aggregation now precedes it. Remove any wording that
  implies search is later reworked for groups.
- Phase 16 (persistent store, formerly 12): note that PostgreSQLJobGroupBackend and the
  JobGroup schema are designed here together with PostgreSQLJobBackend (they co-locate).

Rewrite the bottom "Milestone boundaries" list to match the new order exactly, keeping
the RM ranges attached to each milestone. M1-M10 stays DONE. M11 multi-source stays.
Then M12=Domain hardening, M13=Aggregation, M14=Persistent store, M15=Search, etc.,
through the new Phase 27.

Update the "Parallel work streams" section so phase numbers referenced there match
the new numbering.

Add one sentence to the top "Scope discipline" subsection explaining the value-first
ordering: "Domain value (canonical job, lifecycle) is built before storage, search, and
adapters so those layers are group-aware from day one and require no rework."

---

## FIX 3 — Add SQLite as the lightweight default store/persistence tier

PROBLEM: Only concrete persistence is PostgreSQL. The ladder is InMemory (nothing
survives restart) → Postgres (full server). For "extremely lightweight self-hosted"
there is no single-file middle tier. dlt defaults to DuckDB/SQLite for this reason.

ACTION: In the Persistent store phase (after reorder: Phase 16, formerly 12), add a new
task as the lightweight default, placed BEFORE the PostgreSQL task:

```
### RM-068a SQLiteStore — lightweight default (single file, zero infra)
- `SQLiteStore` in `infrastructure/stores/sqlite.py` implementing `StoreConnector`
  via the `SQLStoreAdapter` (RM-069) with an `aiosqlite` connection.
- Single-file database: `store_backend=sqlite`, `store_path=.runtime/job_ftch.db`.
- Default persistent backend for self-hosted single-node use — survives restarts,
  zero external service. `aiosqlite` is a tiny dependency (no server).
- Reuses the DBMS-agnostic `SQLStoreAdapter`; proves the adapter is truly driver-agnostic.
- `CREATE TABLE IF NOT EXISTS` on startup; same `jf_kv` / `jf_set` schema.
```

Also add, in the Search phase (after reorder: Phase 17, formerly 14), a SQLite job
backend + FTS5 path as the lightweight search tier, placed alongside the PostgreSQL one:

```
### RM-079a SQLiteJobBackend + FTS5 — lightweight search tier
- `SQLiteJobBackend` in `infrastructure/backends/jobs/sqlite.py` implementing
  `JobPersistenceBackend` and `SearchBackend`.
- Uses SQLite FTS5 virtual table for fulltext: `jf_jobs_fts(title, company, description)`.
- Zero extra infrastructure — FTS5 ships with stdlib sqlite3 / aiosqlite.
- Default search backend for single-node deployments; PostgreSQL FTS / pgvector for scale.
- `JobGroup` persisted as JSON in a `jf_job_groups` table (group-aware, matching the
  Postgres backend's surface).
```

Update the phase Purpose lines to mention the tiering: "SQLite (single file, default,
lightweight) and PostgreSQL (server, scale) are both first-class; vector backends are
optional plugins." Update `.env.example` mention: add `store_backend=sqlite` as the
documented default for self-hosted.

Add to the top "Scope discipline" / "Other invariants": "Lightweight default is always
zero-infra: InMemory for dev/tests, SQLite for self-hosted persistence. PostgreSQL,
Qdrant, pgvector are scale-up options, never required for a working install."

---

## FIX 4 — Remove raw embedding vector from the domain `Job` model

PROBLEM: RM-084 attaches `embedding: list[float] | None` to the domain `Job`. A raw
vector is an infrastructure concern; RM-083 already stores vectors in `VectorBackend`
keyed by `job_id` and fetches `Job` separately, so the field is redundant and pollutes
the pure domain model.

ACTION: Edit RM-084 (in the search phase after reorder, Phase 17):
- Remove the line "Result attached to `Job` as `embedding: list[float] | None`."
- Replace with: "The embedding is written only to `VectorBackend.upsert(job_id, vector)`
  keyed by `job_id`. The domain `Job` model never carries the raw vector — embeddings are
  an infrastructure concern, retrieved via `VectorBackend`, not via the domain object."
- Keep the rest of RM-084 (EmbeddingNode, gating by `embedding_enabled`, non-fatal vector
  write) unchanged.

---

## Validation after edits
- File is valid Markdown; phase numbers are sequential with no gaps or duplicates.
- Every "Phase N" cross-reference inside task bodies points to the correct renumbered phase.
- Milestone boundaries list at the bottom matches the new phase order and RM ranges.
- All RM-XXX IDs still present (none lost in the reorder); only phase headings renumbered.
- DONE markers preserved on phases 0-10 and 4.5.
- The four fixes are all applied.
