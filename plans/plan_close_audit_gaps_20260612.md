# Plan: Close Audit Gaps — Structural Fixes

Date: 2026-06-12
Suite baseline: 286 passed, 8 skipped

## Scope

Close the 5 structural gaps identified in the hardcore audit. No speculative work.
All changes must leave `pytest tests -q` green at the end.

---

## Fix 1 — Remove dead exported nodes (trivial, High value)

**Problem:** Three public names in `job_ftch/nodes/__init__.py` are dead:
- `AIRoleRelevanceNode` (imported from `nodes/relevance.py`, never wired into `build_nodes`)
- `HeuristicTriageNode` (imported from `nodes/triage.py`, never wired into `build_nodes`)
- `LanguageContextNode` (alias `= SourceContextNode` in `nodes/language_context.py`, never instantiated)

**Files to change:**
1. `job_ftch/nodes/__init__.py` — remove the three dead imports and their entries from `__all__`
2. `job_ftch/nodes/language_context.py` — remove the `LanguageContextNode = SourceContextNode` alias line
3. Verify `AIRoleRelevanceNode` and `HeuristicTriageNode` are not referenced anywhere else in codebase (`rg -rn "AIRoleRelevanceNode|HeuristicTriageNode" job_ftch tests`). If the source files `nodes/relevance.py` and `nodes/triage.py` are otherwise empty or only contain these dead classes, they can be left as internal files — just remove their public exports.

**Do NOT remove the actual node files** (they may be useful as extension points). Only remove the public exports from `__init__.py` and the alias.

---

## Fix 2 — Eliminate if/elif career_site dispatch in registry

**Problem:** `job_ftch/application/registry.py:359-371` has:
```python
def create_source(settings: Settings) -> object:
    load_extensions()
    if settings.source_backend == "career_site":
        ...  # special-case inline factory
    factory = _source_factories.get(settings.source_backend)
```

This is explicitly forbidden by AGENTS.md: "No if/elif dispatch by adapter kind in core".

**Fix:** Register `career_site` as a regular entry in `_source_factories` dict using the same pattern as all other backends. The factory lambda/function should build the `CareerSiteSpec` from settings and call `create_source_from_spec`. The `if` branch is then deleted.

**Files to change:**
1. `job_ftch/application/registry.py` — add `"career_site"` to `_source_factories` dict, remove the `if settings.source_backend == "career_site"` branch from `create_source()`. The factory function needs access to `settings.career_site_url` and `settings.pipeline_max_items_per_run` — use a closure or inline lambda.

**Validation:** Existing tests that use `career_site` source must still pass.

---

## Fix 3 — Fix funnel ordering: move JobAggregationNode before scoring

**Problem:** Current order in `builder.py:build_nodes()`:
```
...CompensationParsingNode → JobLifecycleNode → MultiProfileMatchNode → RiskScoringNode → QualityScoringNode → JobValidationNode → JobAggregationNode → RoutingNode
```

Master plan requires Aggregation BEFORE scoring (plan nodes 8→9→10):
```
AggregationNode(8) → MatchScoringNode(9) → RiskAndQualityNode(10)
```

The reason: aggregation assigns `group_id` and merge confidence, which should be available to scoring (plan §Penalties: "probable duplicate penalty if unresolved"). Currently `group_id` is assigned AFTER scoring, making cross-source dedup signals invisible to the match/risk scoring step.

**Fix:** Reorder the `nodes` list in `job_ftch/application/builder.py:build_nodes()` to:
```
...CompensationParsingNode → JobLifecycleNode → JobAggregationNode → MultiProfileMatchNode → RiskScoringNode → QualityScoringNode → JobValidationNode → RoutingNode
```

`JobAggregationNode` moves from position 15 (after validation) to position 11 (after lifecycle, before match scoring).

**Files to change:**
1. `job_ftch/application/builder.py` — reorder the `nodes` list in `build_nodes()`

**Validation:**
- All existing tests must still pass (the node itself doesn't change, just position)
- Specifically verify `tests/test_phase14_aggregation.py` and any tests that check `group_id` is set after pipeline run

---

## Fix 4 — Consolidate routing thresholds — remove hardcodes, use Settings

**Problem:**
- `nodes/routing.py:27-31`: `accept_threshold=0.85`, `review_threshold=0.5` hardcoded as constructor defaults, never sourced from Settings
- `nodes/routing.py:56`: `quality < 0.6` magic constant
- `builder.py:528-549`: parallel `_needs_review`/`_should_post` predicates use `settings.review_max_quality_score` and `settings.posting_min_quality_score` for overlapping decisions
- Two sources of truth: `RoutingNode` sets `routing_decision`, sink predicates re-evaluate independently

**Fix (minimal, non-breaking):**
1. In `job_ftch/config.py` — add three new optional settings (if not already present):
   - `routing_accept_threshold: float = Field(default=0.85, ge=0.0, le=1.0)`
   - `routing_review_threshold: float = Field(default=0.5, ge=0.0, le=1.0)`
   - `routing_quality_override_threshold: float = Field(default=0.6, ge=0.0, le=1.0)`
2. In `job_ftch/domain/tenant.py` — add the same three fields with matching defaults
3. In `job_ftch/application/builder.py` — pass the three values from `settings` when constructing `RoutingNode()`:
   ```python
   RoutingNode(
       accept_threshold=settings.routing_accept_threshold,
       review_threshold=settings.routing_review_threshold,
       quality_override_threshold=settings.routing_quality_override_threshold,
   )
   ```
4. In `job_ftch/nodes/routing.py` — add `quality_override_threshold` constructor parameter (default 0.6), use it in the quality override check instead of the magic `0.6`

The sink predicates (`_needs_review`, `_should_post`) in `builder.py` are LEFT AS-IS for now — they serve a different role (sink-level filtering after routing_decision is already set). The key win is that RoutingNode thresholds are now configurable per-tenant via YAML config.

**Files to change:**
1. `job_ftch/config.py`
2. `job_ftch/domain/tenant.py`
3. `job_ftch/application/builder.py` — RoutingNode construction call
4. `job_ftch/nodes/routing.py` — add `quality_override_threshold` parameter

---

## Fix 5 — Fix draft_to_record raw→normalized field mislabeling

**Problem:** `job_ftch/domain/contracts.py:83,106`:
```python
title_normalized=draft.title_raw,    # wrong: raw value in "normalized" field
description_clean=draft.description_raw,  # wrong: raw value in "clean" field
```

This means any consumer reading `title_normalized` or `description_clean` on a freshly converted record gets raw text mislabeled as normalized/clean. Normalization nodes run AFTER draft_to_record and correctly overwrite these fields — but the initial misleading assignment still risks confusing code that reads these fields before normalization completes.

**Fix:** Change the initial mapping to use `None` or an empty sentinel, OR document with a clear comment that these are intentionally set to raw as the pre-normalization placeholder. The pragmatic fix given the pipeline order (normalization nodes DO run after and overwrite these) is to add an inline comment making this explicit, and optionally rename to emphasize the "pre-normalization" state:

In `job_ftch/domain/contracts.py`:
- Change `title_normalized=draft.title_raw` → `title_normalized=draft.title_raw,  # pre-normalization placeholder; overwritten by TitleCompanyNormalizationNode`
- Change `description_clean=draft.description_raw` → `description_clean=draft.description_raw,  # pre-normalization placeholder; overwritten by normalization pass`

This is a documentation fix rather than a behavioral change (the pipeline behavior is correct, the naming is misleading).

**Files to change:**
1. `job_ftch/domain/contracts.py` — add clarifying inline comments to lines 83 and 106

---

## Execution order

1. Fix 1 (dead nodes) — safest, do first
2. Fix 2 (if/elif dispatch) — isolated registry change
3. Fix 5 (contracts comment) — one-line documentation fix
4. Fix 4 (routing thresholds) — config/domain/builder/node changes
5. Fix 3 (funnel ordering) — reorder only, validate with tests

After each fix: run `python -m pytest tests -q --tb=short` and confirm green.

## Success criteria

- `python -m pytest tests -q` passes with >= 286 tests, 0 failures
- `rg "AIRoleRelevanceNode|HeuristicTriageNode" job_ftch/nodes/__init__.py` returns no matches
- `rg "if settings.source_backend == .career_site" job_ftch/application/registry.py` returns no matches
- `JobAggregationNode` appears BEFORE `MultiProfileMatchNode` in `build_nodes()` node list
- `RoutingNode()` in builder receives threshold values from settings, not defaults
- `rg "accept_threshold|review_threshold|routing_quality" job_ftch/config.py` shows the new fields
