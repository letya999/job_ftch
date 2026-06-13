# Plan A — Critical Fixes: O(n) scan, Stage typing, RoutingNode, merge_confidence, ADR-024

## Goal
Fix the four 🔴 and two 🟠 issues from the master plan review. All changes must keep existing
tests green and not break any public API.

---

## Issue 1 — O(n) fuzzy group scan (CRITICAL scalability bug)

### Problem
`job_ftch/nodes/aggregation.py` `_find_or_create_group` calls
`await self._store.list_groups(limit=1000)` and iterates all groups for fuzzy matching.
This is O(n) and silently misses matches beyond 1000 groups.

### Fix
**A) Add blocking key support to `JobGroupStore` contract.**

File: `job_ftch/application/contracts.py`
- Add method to `JobGroupStore` protocol:
  `async def find_by_blocking_key(self, key: str, limit: int = 50) -> list[JobGroup]`

**B) Compute blocking key in domain.**

File: `job_ftch/domain/job_group.py`
- Add function:
  ```python
  def compute_blocking_key(job: JobRecord) -> str:
      """Builds a coarse blocking key: normalized company + first word of normalized title."""
      company = (job.company_canonical or job.company or "").lower().strip()
      title_first = (job.title_normalized or job.title or "").lower().split()[:1]
      return sha256(f"{company}|{' '.join(title_first)}".encode()).hexdigest()[:16]
  ```

**C) Update `JobAggregationNode`.**

File: `job_ftch/nodes/aggregation.py`
- Replace `list_groups(limit=1000)` with `find_by_blocking_key(blocking_key, limit=50)`
- Compute blocking key before fuzzy scan
- Remove the O(n) full scan entirely

**D) Update in-memory and SQLite/Postgres store implementations.**

Files:
- `job_ftch/infrastructure/stores/job_group_store.py`
- Add `find_by_blocking_key` to all `JobGroupStore` implementations
  - In-memory: filter by matching key prefix in a secondary dict `{blocking_key: [group_id]}`
  - Persistent stores: add index on `blocking_key` column, implement query

**E) Update `JobGroup` model to carry blocking_key.**

File: `job_ftch/domain/job_group.py`
- Add optional field `blocking_key: str | None = None` to `JobGroup`
- Populate it in `create_job_group` and `merge_job_into_group`

---

## Issue 2 — Stage[In, Out] type safety collapse in builder

### Problem
`job_ftch/application/builder.py` `build_nodes()` casts all nodes (including type-changing ones
like `TitleCompanyNormalizationNode: JobDraft -> JobRecord`) to
`cast(Sequence[ProcessingNode[object]], nodes)`. This loses all type safety.

`ProcessingNode` is `Stage[T, T]` (same-type), which is wrong for type-changing nodes.

### Fix
**A) Add `TypeChangingNode` protocol to contracts.**

File: `job_ftch/application/contracts.py`
- Add:
  ```python
  @runtime_checkable
  class TypeChangingNode(Stage[StageInput, StageOutput], Protocol[StageInput, StageOutput]):
      """A pipeline node that transitions between two distinct payload types."""
  ```

**B) Make normalization nodes declare their types explicitly.**

File: `job_ftch/nodes/job_normalization.py`
- `TitleCompanyNormalizationNode` should declare:
  `class TitleCompanyNormalizationNode(TypeChangingNode[JobDraft, JobRecord]):`
  (structural only — no runtime change needed, just explicit annotation)

**C) Update `Pipeline` to accept `Stage[Any, Any]` sequence.**

File: `job_ftch/application/pipeline.py`
- Change `nodes: Sequence[ProcessingNode[Any]]` parameter in `Pipeline.__init__` to
  `nodes: Sequence[Stage[Any, Any]]`
- Keep existing behaviour, just widen the type to accept type-changing stages

**D) Update `PipelineBuilder`.**

File: `job_ftch/application/builder.py`
- Change `_stages: list[ProcessingNode[Any]]` to `list[Stage[Any, Any]]`
- Remove the `cast(Sequence[ProcessingNode[object]], nodes)` in `build_nodes`
- Return `tuple[SanitizingNode[RawItem], Sequence[Stage[Any, Any]]]` from `build_nodes`

**E) Update `AGENTS.md` rule.**

File: `AGENTS.md`
- Change "implement `Stage` / `ProcessingNode` Protocol" to clarify:
  - Same-type nodes: implement `ProcessingNode[T]`
  - Type-changing nodes (e.g. extraction/normalization): implement `Stage[In, Out]` directly

---

## Issue 3 — RoutingNode absent as pipeline node

### Problem
The master plan (ADR-024) requires `RoutingNode` as Node 12 with deterministic reason codes.
Currently routing is split between `JobValidationNode` (drops via exception) and
`RoutingSink` (fan-out by quality predicate), with no unified reason audit trail.

### Fix
**A) Create `RoutingNode`.**

File: `job_ftch/nodes/routing.py` (NEW FILE)
- Class `RoutingNode` implements `Stage[JobRecord, JobRecord]`
- Accepts a `ProfileCatalog` and routing thresholds (from Settings or direct)
- Sets `routing_decision` on `JobRecord` to `MatchDecision.ACCEPT/REVIEW/REJECT`
- Appends structured `review_reasons` for every routing decision
- Does NOT drop items (returns them annotated; dropping is still `JobValidationNode`'s job)
- Declares `ROUTING_REASON_CODES` as module-level constants dict

**B) Expose in `job_ftch/nodes/__init__.py`.**

**C) Wire into `build_nodes()` in builder.**

File: `job_ftch/application/builder.py`
- Add `RoutingNode` AFTER `JobAggregationNode` at the end of the node list
- Import from `job_ftch.nodes.routing`

**D) Update `build_output_sinks` predicates to read `routing_decision` from record** (already set by RoutingNode), so the `RoutingSink` predicates become simpler: just check `job.routing_decision`.

---

## Issue 4 — Irreversible merge without merge_confidence

### Problem
`merge_job_into_group` has no confidence signal. All merges are final. Plan requires
`merge_confidence` and reversibility.

### Fix
**A) Add `merge_confidence` to `JobGroup`.**

File: `job_ftch/domain/job_group.py`
- Add `merge_confidence: float = Field(default=1.0, ge=0.0, le=1.0)` to `JobGroup`
- Add `lifecycle_status: str = "active"` to `JobGroup` (values: `active`, `stale`, `closed`)

**B) Pass confidence from aggregation node.**

File: `job_ftch/nodes/aggregation.py`
- Exact URL match → `merge_confidence=1.0`
- Exact identity fingerprint → `merge_confidence=0.95`
- Fuzzy title+company match → `merge_confidence = fuzz_score / 100.0 * 0.85`
- New group → `merge_confidence=1.0`

**C) Update `merge_job_into_group` signature.**

File: `job_ftch/domain/job_group.py`
- `def merge_job_into_group(group: JobGroup, job: JobRecord, *, merge_confidence: float = 1.0) -> JobGroup:`
- Store the minimum confidence seen across all merges in the group (most conservative)

**D) Update `JobGroupStore.merge()` protocol.**

File: `job_ftch/application/contracts.py`
- Add `merge_confidence: float = 1.0` parameter to `JobGroupStore.merge()` protocol method

---

## Issue 5 — ADR-024 status PROPOSED → ACCEPTED

### Problem
ADR-024 is status PROPOSED but the code already partially implements it.
This is a governance gap.

### Fix

File: `docs/adr/024-canonical-job-contract-and-matching-funnel.md`
- Change `**Status**: PROPOSED` → `**Status**: ACCEPTED`
- Add a note at the top: `> Updated: 2026-06-12. Status changed to ACCEPTED; implementation
  > underway on branch feat/semantic-job-pipeline.`

---

## Issue 6 — SourceContextNode incomplete (LanguageContextNode is too narrow)

### Problem
`LanguageContextNode` only detects language. Master plan Node 1 `SourceContextNode` must:
- classify source family
- attach source trust hints
- attach source parsing hints
- attach cheap source-level metadata

### Fix

**A) Rename and expand.**

File: `job_ftch/nodes/language_context.py`
- Rename class to `SourceContextNode` (keep `LanguageContextNode` as alias for backwards compat)
- Add source trust scoring logic:
  - `CAREER_SITE` → trust=0.9
  - `TELEGRAM_CHANNEL` → trust=0.7
  - `TELEGRAM_GROUP` → trust=0.5
  - `TELEGRAM_COMMENT` → trust=0.3
  - `DEBUG` → trust=1.0
- Add source family classification: attach `"source_family"` to metadata
  (`"telegram"` | `"career_site"` | `"api"` | `"debug"`)
- Add parsing hints: for Telegram sources, attach `"has_hashtags"`, `"has_urls"`,
  `"approx_word_count"` to metadata
- Keep all existing language detection logic unchanged

**B) Update `__init__.py` exports.**

File: `job_ftch/nodes/__init__.py`
- Export `SourceContextNode` alongside `LanguageContextNode`

**C) Update `builder.py`.**

File: `job_ftch/application/builder.py`
- Change `LanguageContextNode()` import and instantiation to `SourceContextNode()`
- Keep `LanguageContextNode` import as alias (for any external code)

---

## Files to create/modify

| File | Action |
|------|--------|
| `job_ftch/domain/job_group.py` | Add `blocking_key`, `merge_confidence`, `lifecycle_status` to `JobGroup`; add `compute_blocking_key()`; update `create_job_group`, `merge_job_into_group` |
| `job_ftch/application/contracts.py` | Add `TypeChangingNode` protocol; add `find_by_blocking_key` to `JobGroupStore`; add `merge_confidence` param to `merge` |
| `job_ftch/application/pipeline.py` | Widen `nodes` type to `Sequence[Stage[Any, Any]]` |
| `job_ftch/application/builder.py` | Fix `_stages` type, fix `build_nodes` return type, add `RoutingNode`, use `SourceContextNode` |
| `job_ftch/nodes/language_context.py` | Expand to `SourceContextNode` with trust/family/hints; alias `LanguageContextNode` |
| `job_ftch/nodes/aggregation.py` | Replace O(n) scan with `find_by_blocking_key`; pass `merge_confidence` |
| `job_ftch/nodes/routing.py` | NEW — `RoutingNode` implementation |
| `job_ftch/nodes/__init__.py` | Export `SourceContextNode`, `RoutingNode` |
| `job_ftch/nodes/job_normalization.py` | Annotate `TitleCompanyNormalizationNode` with `Stage[JobDraft, JobRecord]` |
| `job_ftch/infrastructure/stores/job_group_store.py` | Add `find_by_blocking_key` with blocking_key index |
| `docs/adr/024-canonical-job-contract-and-matching-funnel.md` | Status PROPOSED → ACCEPTED |
| `AGENTS.md` | Fix stale `Stage`/`ProcessingNode` guidance |
| `tests/test_phase14_aggregation.py` | Update tests for new `merge_confidence`, `blocking_key`, `lifecycle_status` fields |
| `tests/test_phase567_contracts.py` | Add tests for `RoutingNode`, `SourceContextNode` expansion, `TypeChangingNode` |

---

## Constraints

- All existing tests must remain green
- `domain/` imports only `pydantic` and stdlib — no new infrastructure imports
- `LanguageContextNode` must remain importable as an alias
- No breaking changes to `JobGroup` serialization (new fields must be optional with defaults)
- `RoutingNode` must be a no-op (pass-through) if `profile_scores` is empty — do not drop
- `find_by_blocking_key` in in-memory store uses simple dict secondary index, no external deps

---

## Verification

After implementation, run:
```
pytest tests/ -x -q
```
Expected: all existing tests pass, new tests for RoutingNode/SourceContextNode/blocking_key pass.
