# Plan B — Canonical JobRecord Fields + JobGroup lifecycle

## Goal
Add the ~15 missing fields identified in the master plan review to `JobRecord` and
`JobGroup`. All new fields must be optional with `None` defaults to avoid breaking
any existing tests or serialization.

---

## Issue 1 — Missing fields in JobRecord canonical vacancy model

### Current state
`JobRecord` inherits `Job` (flat legacy model). Missing canonical fields from master plan:

**Role section:**
- `leadership_level: str | None` — e.g. "head_of_engineering", "vp", "c_level"
- `ic_or_manager: str | None` — "ic" | "manager" | "hybrid" | None

**Employer section:**
- `company_type: str | None` — "startup", "enterprise", "agency", "nonprofit", etc.
- `team_size_hint: str | None` — "1-10", "11-50", "51-200", "200+", etc.

**Skills section:**
- `domain_knowledge: tuple[str, ...] = ()` — domain-specific expertise ("fintech", "healthcare", etc.)
- `soft_skills: tuple[str, ...]  = ()` — "communication", "leadership", etc.

**Requirements section:**
- `years_experience: int | None` — minimum years
- `education: str | None` — "bachelor", "master", "phd", "any", etc.
- `certifications: tuple[str, ...] = ()` — required certifications

**Location section:**
- `remote_restrictions: str | None` — "EU only", "US timezone", etc.
- `relocation: bool | None` — relocation offered
- `visa_support: bool | None` — visa sponsorship offered

**AggregationBlock on JobRecord:**
- Already has `group_id` ✅. Add:
- `aggregate_source_count: int = 1` — how many sources contributed to this record
- `aggregation_confidence: float | None` — confidence that this group assignment is correct

### Fix

**File: `job_ftch/domain/models.py`**

Add all missing fields to the `Job` base class (since `JobRecord` inherits from it),
placed logically after existing related fields. All new fields must be:
- `Optional` with `= None` or `= ()` defaults
- `frozen=True` compatible (already satisfied by model_config)
- No new required validators unless trivial

Specific additions to `Job` model:
```python
# Role extensions
leadership_level: str | None = None
ic_or_manager: str | None = None

# Employer extensions
company_type: str | None = None
team_size_hint: str | None = None

# Skills extensions
domain_knowledge: tuple[str, ...] = ()
soft_skills: tuple[str, ...] = ()

# Requirements extensions
years_experience: int | None = Field(default=None, ge=0)
education: str | None = None
certifications: tuple[str, ...] = ()

# Location extensions
remote_restrictions: str | None = None
relocation: bool | None = None
visa_support: bool | None = None
```

Add to `JobRecord` (not `Job`):
```python
# Aggregation block on record level
aggregate_source_count: int = Field(default=1, ge=1)
aggregation_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
```

Update `validate_job` in `Job` to normalize new optional text fields:
Add these to the `optional_text_fields` tuple:
`"leadership_level", "ic_or_manager", "company_type", "team_size_hint",
 "education", "remote_restrictions"`

And normalize new tuples in `validate_job`:
```python
object.__setattr__(self, "domain_knowledge", _normalized_tuple(self.domain_knowledge))
object.__setattr__(self, "soft_skills", _normalized_tuple(self.soft_skills))
object.__setattr__(self, "certifications", _normalized_tuple(self.certifications))
```

---

## Issue 2 — Missing lifecycle_status on JobGroup (already added in Plan A)

Plan A already added `lifecycle_status: str = "active"` and `merge_confidence` to `JobGroup`.
This issue is resolved. ✅

Verify that `JobGroup` now has:
- `lifecycle_status: str = "active"` ✅ (from Plan A)
- `merge_confidence: float = Field(default=1.0, ge=0.0, le=1.0)` ✅ (from Plan A)
- `blocking_key: str | None = None` ✅ (from Plan A)

---

## Issue 3 — Update dependent domain contracts

**File: `job_ftch/domain/contracts.py`**

Update `job_to_draft` and `draft_to_record` conversion helpers to pass through new fields
if present on the source model. Since `JobDraft` doesn't have all these fields, the
converters can safely ignore them (they default to None/empty tuple in the target).

No changes needed to `job_to_draft` — it only maps fields that exist on `JobDraft`.

Update `draft_to_record` comment to note that new canonical fields are defaulted to None
and must be populated by normalization nodes.

---

## Issue 4 — Update JSON schema export

**File: `scripts/export_schema.py`**

After adding fields to models, the schema export script should automatically pick them up
via `model_json_schema()`. No code changes needed — just verify.

Add `$id` URI fields to the exported schemas:

```python
schema["$id"] = f"https://job-ftch.dev/schemas/{model.__name__}.schema.json"
schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
```

Add these two lines for each exported schema in `export_schema.py`.

---

## Issue 5 — Update test_schema_export.py

**File: `tests/test_schema_export.py`**

Add assertions for new fields in exported schemas:
- `assert "leadership_level" in record_schema["properties"]`
- `assert "aggregate_source_count" in record_schema["properties"]`
- `assert "years_experience" in record_schema["properties"]`

Add assertions for `$id` presence:
- `assert "$id" in record_schema`
- `assert "$id" in raw_schema`

---

## Issue 6 — Update ExtractionNode to populate new fields where possible

**File: `job_ftch/nodes/extraction.py`** (light touch only)

The `HeuristicJobExtractor` and LLM-based extractor should attempt to populate:
- `years_experience` — parse from common patterns like "3+ years", "от 3 лет"
- `education` — parse from "degree", "bachelor", "высшее образование" patterns
- `relocation` — detect if "relocation" or "релокация" mentioned
- `visa_support` — detect if "visa" or "виза" mentioned

These should be added to the heuristic extractor in:
**File: `job_ftch/infrastructure/llm/heuristic.py`**

Add simple regex-based detection in `_extract_heuristic`:
```python
# years_experience: look for "X+ years" or "от X лет"
# education: look for degree keywords
# relocation: look for "relocation" keyword
# visa_support: look for "visa" keyword
```

Return these as new fields on the extracted `JobDraft`.

But `JobDraft` also needs to carry these fields! Update:
**File: `job_ftch/domain/models.py`** — add to `JobDraft` model:
```python
years_experience: int | None = Field(default=None, ge=0)
education: str | None = None
relocation: bool | None = None
visa_support: bool | None = None
domain_knowledge: tuple[str, ...] = ()
soft_skills: tuple[str, ...] = ()
certifications: tuple[str, ...] = ()
leadership_level: str | None = None
ic_or_manager: str | None = None
company_type: str | None = None
team_size_hint: str | None = None
remote_restrictions: str | None = None
```

Update `validate_draft` in `JobDraft` to normalize these fields:
- Add new text fields to `optional_text_fields` tuple
- Add new tuple fields to normalization calls

---

## Issue 7 — Update draft_to_record converter to pass new fields through

**File: `job_ftch/domain/contracts.py`**

Update `draft_to_record` to pass all new `JobDraft` fields to `JobRecord`:
```python
years_experience=draft.years_experience,
education=draft.education,
relocation=draft.relocation,
visa_support=draft.visa_support,
domain_knowledge=draft.domain_knowledge,
soft_skills=draft.soft_skills,
certifications=draft.certifications,
leadership_level=draft.leadership_level,
ic_or_manager=draft.ic_or_manager,
company_type=draft.company_type,
team_size_hint=draft.team_size_hint,
remote_restrictions=draft.remote_restrictions,
```

---

## Files to create/modify

| File | Action |
|------|--------|
| `job_ftch/domain/models.py` | Add new fields to `Job`, `JobDraft`, `JobRecord`; update validators |
| `job_ftch/domain/contracts.py` | Update `draft_to_record` to pass new fields through |
| `job_ftch/infrastructure/llm/heuristic.py` | Add simple regex extraction for years_experience, education, relocation, visa_support |
| `scripts/export_schema.py` | Add `$id` and `$schema` to exported JSON schemas |
| `tests/test_schema_export.py` | Add assertions for new fields and `$id` |

---

## Constraints

- ALL new fields must be optional with `None` or `()` defaults — NO breaking changes
- `domain/` imports only `pydantic` and stdlib — no changes to import rules
- Do NOT rename or remove any existing fields
- Do NOT change any existing validators in a way that breaks current behavior
- `extra="forbid"` is set on models — all new fields MUST be declared, or pass-through
  via `model_copy` with `update=` will fail
- Keep `$id` value as a simple string constant (no runtime URL fetching)

---

## Verification

After implementation:
```
pytest tests/test_schema_export.py tests/test_phase567_contracts.py tests/test_extraction.py -x -q
```
Expected: all pass, new fields present in schema export, `$id` in schemas.
