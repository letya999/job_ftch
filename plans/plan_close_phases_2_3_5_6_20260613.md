# Plan: Close Remaining Gaps — Phases 2, 3, 5, 6

Date: 2026-06-13
Suite baseline: 295 passed, 8 skipped

## Summary of actual remaining gaps (verified against live code)

- **Phase 2:** 4 missing canonical fields on JobRecord + `schema_version` on RawItem/JobDraft
- **Phase 3:** `ExtractionValidationNode` is imported in builder.py (line 63) but NOT in the nodes list — regression, must re-wire
- **Phase 4:** Already closed — OntologyNormalizer, SkillNormalizationNode, alias tables all exist
- **Phase 5:** Already closed — `JobAggregationNode` uses `compute_blocking_key` + `find_by_blocking_key` + rapidfuzz fuzzy match; `_blocking_index` exists in InMemoryJobGroupStore. Both Layer 2 and 3 are implemented.
- **Phase 6:** 5 missing entry-point groups in pyproject.toml + PluginMetadata type + minimal plugin templates

---

## Fix A — Phase 2: Missing canonical fields + schema_version (domain/models.py)

**NOTE:** `gross_or_net`, `bonus`, `equity` are already in `CompensationRange` model — skip these.
**NOTE:** `must_have_skills`, `nice_to_have_skills` — check if they exist under different names before adding.

### Step A1: Check what's really missing

Before editing, run:
```python
from job_ftch.domain.models import JobRecord
fields = JobRecord.model_fields
print([f for f in ['role_specialization','must_have_skills','nice_to_have_skills','culture_summary'] if f not in fields])
```

### Step A2: Add missing fields to `job_ftch/domain/models.py`

Add these fields to the `Job` base class (around line 288-310, after `role_family`):
- `role_specialization: str | None = None` — e.g. "backend", "frontend", "fullstack"
- `culture_summary: str | None = None` — brief extracted culture description

Add to the `skills` section (if `must_have_skills` / `nice_to_have_skills` truly absent):
- `must_have_skills: tuple[str, ...] = ()` — extracted must-have skill names
- `nice_to_have_skills: tuple[str, ...] = ()` — extracted nice-to-have skill names

### Step A3: Add `schema_version` to RawItem and JobDraft

In `job_ftch/domain/models.py`:
- `RawItem` class: add `schema_version: str = "1"` field
- `JobDraft` class (if it exists as a class): add `schema_version: str = "1"` field

Check where `RawItem` and `JobDraft` are defined first (use read_file on domain/models.py).

### Step A4: Update `scripts/export_schema.py`

After adding fields, run `python scripts/export_schema.py` to verify schema export still works.

### Step A5: Update `tests/test_schema_export.py`

Add assertions for new fields:
- `assert "role_specialization" in record_schema["properties"]`
- `assert "culture_summary" in record_schema["properties"]`

---

## Fix B — Phase 3: Re-wire ExtractionValidationNode

**Problem:** `ExtractionValidationNode` is imported in `job_ftch/application/builder.py` line 63 but missing from the `nodes` list in `build_nodes()`.

Current order (broken):
```python
ExtractionNode(llm),
TitleCompanyNormalizationNode(),   # ExtractionValidationNode is MISSING between these
```

**Fix:** In `job_ftch/application/builder.py`, in `build_nodes()` nodes list, insert `ExtractionValidationNode()` between `ExtractionNode(llm)` and `TitleCompanyNormalizationNode()`:

```python
ExtractionNode(llm),
ExtractionValidationNode(),        # restore: was removed by mistake
TitleCompanyNormalizationNode(),
```

**Files to change:**
1. `job_ftch/application/builder.py` — insert `ExtractionValidationNode()` at correct position

**Validation:** Run `python -m pytest tests/test_extraction.py tests/test_job_quality.py -q -o addopts="" --tb=short`

---

## Fix C — Phase 6: Plugin SDK completion

### Step C1: Add missing entry-point groups to `pyproject.toml`

Find the `[project.entry-points]` section and add:
```toml
[project.entry-points."job_ftch.extractors"]
# Extractor plugins go here

[project.entry-points."job_ftch.classifiers"]
# Classifier plugins go here

[project.entry-points."job_ftch.normalizers"]
# Normalizer plugins go here

[project.entry-points."job_ftch.scorers"]
# Scorer plugins go here

[project.entry-points."job_ftch.notification_targets"]
# Notification target plugins go here
```

### Step C2: Add `PluginMetadata` dataclass to `job_ftch/application/contracts.py`

Add after the existing Protocol definitions:

```python
@dataclass(frozen=True)
class PluginMetadata:
    """Metadata manifest for any job_ftch plugin."""
    name: str              # unique plugin identifier
    version: str           # semver string
    plugin_type: str       # "source" | "sink" | "extractor" | "classifier" | "normalizer" | "scorer" | "notification_target"
    description: str
    author: str = ""
    requires_extras: tuple[str, ...] = ()   # extras groups needed: ("openai",)
    entry_point_group: str = ""             # e.g. "job_ftch.sources"
```

Make sure to add `from dataclasses import dataclass` import if not present.

### Step C3: Export `PluginMetadata` from `job_ftch/application/__init__.py` or `job_ftch/__init__.py`

Check where public exports live and add `PluginMetadata` to the public API.

### Step C4: Create plugin template documents

Create `docs/plugin_template/` directory with 4 template files:

**`docs/plugin_template/source_plugin.md`** — template for a custom Source plugin:
- Shows `@register_source("my_source")` decorator usage
- Minimal `MySource(Source[RawItem])` class skeleton with `fetch()` method
- Shows `SourceSpec` spec pattern
- Shows entry-point registration in `pyproject.toml`
- Shows how to write a contract test

**`docs/plugin_template/sink_plugin.md`** — template for a custom Sink plugin:
- Shows `@register_sink("my_sink")` pattern
- Minimal `MySink(Sink[JobRecord])` with `emit()` and `flush()`
- Entry-point group: `job_ftch.sinks`

**`docs/plugin_template/scorer_plugin.md`** — template for a custom Scorer/Normalizer plugin:
- Shows how to implement a custom scoring node as `Stage[JobRecord, JobRecord]`
- Shows entry-point group: `job_ftch.scorers`
- Shows `PluginMetadata` usage

**`docs/plugin_template/README.md`** — index of all template types with when to use each one

### Step C5: Add contract test base for plugins

Create `tests/test_plugin_contracts.py` with:
- `TestSourcePluginContract` — base test class with abstract fixtures that any Source plugin should satisfy:
  - `fetch()` returns `AsyncIterator[RawItem]`
  - Each item has required fields set (`source_kind`, `source_name`, `external_id`, `text`)
  - Plugin handles empty result gracefully
- `TestSinkPluginContract` — base for Sink plugins:
  - `emit()` accepts `JobRecord`
  - `flush()` is idempotent
- One concrete test that verifies the `InMemoryStore` satisfies the `Store` Protocol (as a reference implementation example)

---

## Execution order

1. Fix B (ExtractionValidationNode) — single line change, verify with targeted tests
2. Fix A (missing fields) — domain model changes, run schema export after
3. Fix C1 (pyproject.toml EP groups) — safe config change
4. Fix C2 (PluginMetadata) — add to contracts.py
5. Fix C3 (export PluginMetadata) — wire into public API
6. Fix C4 (plugin templates) — docs only, no code
7. Fix C5 (contract test base) — new test file

After each fix, run targeted tests. After all: full suite.

---

## Validation commands

```bash
# After Fix B:
python -m pytest tests/test_extraction.py tests/test_job_quality.py -q -o addopts="" --tb=short

# After Fix A:
python -m pytest tests/test_schema_export.py tests/test_domain_models.py -q -o addopts="" --tb=short
python scripts/export_schema.py  # must not error

# After Fix C:
python -c "from job_ftch.application.contracts import PluginMetadata; print(PluginMetadata('test','1.0','source','desc'))"
python -m pytest tests/test_plugin_contracts.py -q -o addopts="" --tb=short

# Full suite:
python -m pytest tests -q -o addopts="" --tb=short
```

## Success criteria

- `python -m pytest tests -q -o addopts="" --tb=short` >= 295 passed, 0 failures
- `ExtractionValidationNode()` is in the nodes list between `ExtractionNode` and `TitleCompanyNormalizationNode` in `builder.py`
- `JobRecord.model_fields` contains `role_specialization` and `culture_summary`
- `RawItem.model_fields` contains `schema_version`
- `python -c "import tomllib; t=tomllib.load(open('pyproject.toml','rb')); print('job_ftch.scorers' in t['project']['entry-points'])"` prints `True`
- `from job_ftch.application.contracts import PluginMetadata` works
- `docs/plugin_template/README.md` exists
- `tests/test_plugin_contracts.py` exists and passes
