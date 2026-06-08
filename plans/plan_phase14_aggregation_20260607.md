# Plan: Phase 14 — Cross-source job aggregation (RM-137..RM-140)

**Branch**: `feat/phase-13` (phase 13 prerequisites included here)
**Date**: 2026-06-07
**Roadmap refs**: RM-137, RM-138, RM-139, RM-140

---

## Goal

Implement cross-source job aggregation so that the same job posted across N sources becomes
ONE canonical `JobGroup` record (enriched from all sources), instead of N separate dropped duplicates.

DedupNode (Phase 4) stays unchanged — it operates on `RawItem` level ("seen this raw text before?").
JobAggregationNode is a new `Stage[Job, Job]` that operates AFTER ExtractionNode — "is this Job
the same vacancy observed from a different source?" The Job PASSES THROUGH; merging is a side-effect.

---

## Prerequisite from Phase 13 (minimum needed for Phase 14)

### Modify `domain/models.py`
- Add `company_canonical: str | None = None` to `Job` model.
  - Placed after `company` field.
  - Evolution annotation in docstring: `evolve` (additive, safe).
  - Validated same way as `company`: strip whitespace, set to None if blank.
  - Used by `JobAggregationNode` matching ladder (step 2: fingerprint by company_canonical + title + location).

---

## RM-137: JobGroup domain model

### Create `domain/job_group.py`

Models (all `frozen=True`, pydantic only + stdlib — no infra imports):

```python
class SourceAttribution(BaseModel):
    source_kind: SourceKind
    source_name: str
    url: AnyHttpUrl | None
    first_seen_at: datetime
    last_seen_at: datetime
```

```python
class JobGroup(BaseModel):
    group_id: str           # sha256 of canonical fingerprint
    canonical_job: Job      # merged best-field record
    jobs: list[Job]         # one per unique source (ordered by source priority)
    source_attributions: list[SourceAttribution]
    source_count: int       # = len(jobs)
    first_seen_at: datetime
    last_seen_at: datetime
```

**Merge policy** (pure function `merge_jobs(jobs: list[Job]) -> Job`):
- Source priority order: CAREER_SITE > TELEGRAM_CHANNEL > TELEGRAM_GROUP > TELEGRAM_COMMENT > DEBUG
- canonical_url: first non-None from highest-priority source
- title, company, company_canonical, location, work_mode: from highest-priority source that has it
- description: longest non-empty description wins
- compensation: first non-None value
- quality_score, relevance_score: max of all
- extraction_status: COMPLETE if any source has it, else PARTIAL
- review_reasons: union of all unique reasons
- metadata: merged dict (higher-priority source wins on key conflict)
- raw_item_id, source_kind, source_name: from the canonical (highest-priority) source
- stable_id: recomputed from the merged Job fields

**`group_id` computation** (pure function `compute_group_fingerprint(job: Job) -> str`):
- sha256 of: `canonical_url | company_canonical | normalized_title | location`
- `normalized_title`: lowercase, strip punctuation, sort words
- If canonical_url is present, that alone is sufficient for the fingerprint

### Update `domain/__init__.py`
- Export `JobGroup`, `SourceAttribution`

---

## RM-138: Cross-source identity matching

### Create `application/identity.py`

```python
class JobIdentityMatcher:
    """
    Three-step matching ladder on Job objects (post-extraction).
    Step 1: exact canonical_url match — O(1) via url index in JobGroupStore
    Step 2: fingerprint match — sha256(company_canonical + normalized_title + location)
    Step 3: fuzzy title match via rapidfuzz (Levenshtein) — only for candidates from step 2 near-misses
    """
    def __init__(
        self,
        store: "JobGroupStore",
        fuzzy_title_threshold: float = 85.0,
        enable_fuzzy: bool = True,
    ) -> None: ...

    async def find_matching_group(self, job: Job) -> str | None:
        """Return group_id of matching JobGroup, or None if no match."""
        # Step 1: exact URL match
        # Step 2: fingerprint match
        # Step 3: fuzzy title (if enable_fuzzy and no match yet)
```

Only imports: `domain`, `application.contracts`, stdlib.
`rapidfuzz` is used only in `nodes/aggregation.py` (not here) to keep application/ clean.
Wait — actually rapidfuzz is a third-party lib. Check: application/ can import... "only domain/ + stdlib + pydantic".
Therefore: move fuzzy matching into `nodes/aggregation.py`, keep `identity.py` to steps 1 and 2 only.
`identity.py` calls store methods only; no rapidfuzz import.

### Create `nodes/aggregation.py`

```python
class JobAggregationNode:
    """
    Stage[Job, Job] — cross-source aggregation.
    Job PASSES THROUGH unchanged.
    Side-effect: creates or updates a JobGroup in JobGroupStore.
    Placed AFTER ExtractionNode (and optionally CompanyCanonicalizer from Phase 13).
    """
    def __init__(
        self,
        store: "JobGroupStore",
        fuzzy_title_threshold: float = 85.0,
        enable_fuzzy: bool = True,
    ) -> None: ...

    async def process(self, job: Job) -> Job:
        group_id = await self._find_or_create_group(job)
        return job  # Job always passes through

    async def _find_or_create_group(self, job: Job) -> str:
        # Step 1: exact canonical_url match
        # Step 2: fingerprint match (uses compute_group_fingerprint from domain)
        # Step 3: fuzzy title match via rapidfuzz against candidates
        # On match: store.merge(group_id, job)
        # On no match: store.create(job) -> new JobGroup
```

Imports: `domain`, `application.contracts`, `rapidfuzz` (allowed in nodes/).

---

## RM-139: JobGroupStore protocol + InMemoryJobGroupStore

### Modify `application/contracts.py`
Add `JobGroupStore` Protocol:

```python
@runtime_checkable
class JobGroupStore(Protocol):
    async def get(self, group_id: str) -> JobGroup | None: ...
    async def create(self, job: Job) -> JobGroup: ...
    async def merge(self, group_id: str, job: Job) -> JobGroup: ...
    async def find_by_url(self, canonical_url: str) -> JobGroup | None: ...
    async def find_by_fingerprint(self, fingerprint: str) -> JobGroup | None: ...
    async def list_groups(self, limit: int = 100) -> list[JobGroup]: ...
    async def count(self) -> int: ...
```

### Create `infrastructure/stores/job_group_store.py`

`InMemoryJobGroupStore` implementing `JobGroupStore`:
- `_groups: dict[str, JobGroup]` — keyed by group_id
- `_url_index: dict[str, str]` — canonical_url → group_id
- `_fingerprint_index: dict[str, str]` — fingerprint → group_id
- `create()`: computes fingerprint, creates JobGroup with single job
- `merge()`: adds job to existing group, re-runs merge_jobs(), updates indices
- `find_by_url()`: O(1) via _url_index
- `find_by_fingerprint()`: O(1) via _fingerprint_index

PostgreSQLJobGroupBackend is deferred to Phase 15 (Persistent store).
Add `# TODO(phase-15): PostgreSQLJobGroupBackend` stub comment in the store module.

### Modify `application/registry.py`
Add `@register_job_group_store(name: str)` decorator following existing pattern.

---

## RM-140: Aggregation regression tests

### Create `tests/test_phase14_aggregation.py`

Test scenarios:
1. **Three-source merge**: same job from `telegram_channel`, `career_site`, `telegram_group`
   → 1 JobGroup with `source_count=3`; canonical_job uses career_site fields where available.
2. **URL matching**: two Jobs with same `canonical_url` from different sources → merged.
3. **Fingerprint matching**: two Jobs with same `company_canonical + normalized_title + location`
   but different URLs → merged into one group.
4. **Fuzzy title matching**: title "ML Engineer, Sber" vs "ML Engineer at Sber" → merged
   (Levenshtein above threshold).
5. **No false merge**: two genuinely different jobs (different company + title) → two groups.
6. **DedupNode pass-through + aggregation**: item that passes DedupNode (new raw_item)
   but matches existing JobGroup → merged, NOT dropped. Both assertions required.
7. **RunSummary counters**: `merged_into_group: int`, `new_groups_created: int`
   (add to `application/telemetry.py` RunSummary).

### Update `application/telemetry.py` (RunSummary)
Add fields:
- `new_groups_created: int = 0`
- `merged_into_group: int = 0`

---

## Files to create/modify (summary)

| Action | File |
|--------|------|
| MODIFY | `domain/models.py` — add `company_canonical: str \| None = None` to `Job` |
| CREATE | `domain/job_group.py` — `SourceAttribution`, `JobGroup`, `merge_jobs()`, `compute_group_fingerprint()` |
| MODIFY | `domain/__init__.py` — export `JobGroup`, `SourceAttribution` |
| MODIFY | `application/contracts.py` — add `JobGroupStore` Protocol |
| CREATE | `application/identity.py` — `JobIdentityMatcher` (steps 1-2, no rapidfuzz) |
| MODIFY | `application/registry.py` — add `@register_job_group_store` |
| MODIFY | `application/telemetry.py` — add `new_groups_created`, `merged_into_group` to RunSummary |
| CREATE | `nodes/aggregation.py` — `JobAggregationNode` (Step 3 fuzzy via rapidfuzz) |
| CREATE | `infrastructure/stores/job_group_store.py` — `InMemoryJobGroupStore` |
| CREATE | `tests/test_phase14_aggregation.py` — RM-140 regression tests |

---

## Hard constraints (from AGENTS.md + architecture.md)

- `domain/` imports ONLY pydantic + stdlib. No infra, no rapidfuzz.
- `application/` imports ONLY domain + stdlib + pydantic. No rapidfuzz, no external libs.
- `rapidfuzz` is allowed ONLY in `nodes/aggregation.py`.
- `InMemoryJobGroupStore` lives in `infrastructure/` (not application/).
- `JobGroupStore` Protocol lives in `application/contracts.py`.
- `JobGroup` + `SourceAttribution` + `merge_jobs()` + `compute_group_fingerprint()` live in `domain/`.
- No ORM. No SQLAlchemy.
- PostgreSQLJobGroupBackend deferred (Phase 15).
- All new fields on `Job` have defaults (backward-compatible deserialization).
- All type hints required. mypy strict must pass.
- `uv run ruff check .` and `uv run ruff format --check .` must pass.

---

## Quality gates before commit

1. `uv run ruff check .` — no errors
2. `uv run ruff format --check .` — no formatting issues
3. `uv run mypy .` — strict mode, no new errors
4. `uv run pytest tests/test_phase14_aggregation.py -v` — all tests pass
5. `uv run pytest tests/ -v` — no regressions in existing tests
6. `uv run bandit -r app.py config.py application domain infrastructure nodes sinks -ll` — clean
