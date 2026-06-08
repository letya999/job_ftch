# Plan: Phase 12 — Configurable Filter Profiles (RM-074 to RM-078)

## Context

Branch: `feat/phase-12` (already created, clean — no changes vs main).
Working directory: `C:\Users\User\a_projects\job_ftch`

Purpose: replace all hardcoded keyword lists in `nodes/triage.py` and `nodes/relevance.py`
with config-driven `FilterProfile`. No keyword changes should require code edits after this phase.

All decisions already documented in `docs/adr/013-filter-profile-configurable-relevance.md` (ACCEPTED).

---

## Hard rules (from AGENTS.md + docs/rules.md)

- `domain/` imports only `pydantic` + stdlib. NO yaml import in domain layer.
- `application/` imports only `domain/` + stdlib + `pydantic`.
- `nodes/` imports only `domain/` + `application/`.
- No new top-level dependencies. `pyyaml` is already used lazily in `application/source_loader.py` — follow same pattern.
- Commits: `feat`, `fix`, `chore`, `docs` prefixes only. No AI attribution. No Co-authored-by.
- All keyword lists that currently live in `nodes/` must move into `FilterProfile.default()`.
- Backward compatibility: with no config, behaviour must be identical to pre-Phase-12.

---

## Files to CREATE

### 1. `domain/filter_profile.py`

Pure Pydantic model, no I/O, no yaml/json loading. Only stdlib + pydantic imports.

```python
class FilterProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = "default"
    required_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    allowed_source_kinds: list[SourceKind] | None = None
    min_text_tokens: int = Field(default=3, ge=1)
    min_text_chars: int = Field(default=18, ge=1)
    positive_relevance_keywords: list[str] = Field(default_factory=list)
    negative_relevance_keywords: list[str] = Field(default_factory=list)
    relevance_threshold: float = Field(default=0.0, ge=0.0, le=1.0)

    @classmethod
    def default(cls) -> FilterProfile:
        # Hardcoded defaults that mirror pre-Phase-12 behaviour exactly.
        # positive_relevance_keywords mirrors _POSITIVE_KEYWORDS from nodes/relevance.py
        # negative_relevance_keywords mirrors _NEGATIVE_KEYWORDS from nodes/relevance.py
        # exclude_keywords mirrors _IRRELEVANT_PATTERNS from nodes/triage.py
        # min_text_tokens=3, min_text_chars=18 mirror HeuristicTriageNode constructor defaults
        return cls(
            name="default",
            required_keywords=[],
            exclude_keywords=[
                "subscribe", "follow us", "webinar", "meetup", "conference",
                "course", "training", "newsletter", "digest", "news",
                "podcast", "like and share",
            ],
            allowed_source_kinds=None,
            min_text_tokens=3,
            min_text_chars=18,
            positive_relevance_keywords=[
                "ai", "llm", "genai", "mlops", "ml ", "machine learning",
                "agent", "rag", "prompt", "infra", "platform",
                "data scientist", "ai pm", "ai product",
            ],
            negative_relevance_keywords=[
                "sales", "account executive", "hr", "recruiter",
                "office manager", "marketing",
            ],
            relevance_threshold=0.0,
        )
```

### 2. `application/filter_profile_loader.py`

Loads FilterProfile from YAML or JSON file. Follows exact same lazy-yaml pattern as `application/source_loader.py`.

```python
def load_filter_profile(path: Path) -> FilterProfile:
    text = path.read_text(encoding="utf-8")
    if path.suffix in {".yaml", ".yml"}:
        try:
            import yaml
            data = yaml.safe_load(text)
        except ImportError as exc:
            raise RuntimeError("PyYAML required: pip install pyyaml") from exc
    else:
        data = json.loads(text)
    return FilterProfile.model_validate(data)
```

### 3. `profiles/ai_roles.yaml`

Built-in profile matching `FilterProfile.default()` values — operators can copy and customize.

### 4. `profiles/all_roles.yaml`

No-op passthrough profile: empty positive_relevance_keywords (disables relevance check),
empty negative and exclude lists, no allowed_source_kinds filter.

### 5. `tests/test_phase12_filter_profiles.py`

Regression tests for RM-078 (see test spec below).

---

## Files to MODIFY

### 6. `domain/__init__.py`

Add `from domain.filter_profile import FilterProfile` and add `"FilterProfile"` to `__all__`.

### 7. `nodes/triage.py`

- Remove module-level `_JOB_SIGNAL_PATTERNS` and `_IRRELEVANT_PATTERNS` constants (they move to `FilterProfile.default()`).
- Keep `_COMMENT_SIGNAL_PATTERNS` and `_CAREER_NAVIGATION_PATTERNS` as module-level constants (structural pipeline logic, not operator-tunable signal).
- `HeuristicTriageNode.__init__` signature: `def __init__(self, *, profile: FilterProfile | None = None) -> None:`
- In `__init__`: `self._profile = profile if profile is not None else FilterProfile.default()`
- Remove old `min_text_tokens` and `min_text_chars` constructor params — they come from profile now.
- Update `process()`: use `profile.min_text_tokens`, `profile.min_text_chars`, `profile.positive_relevance_keywords` (for job signal), `profile.exclude_keywords` (for irrelevant check), `profile.allowed_source_kinds`.
- `required_keywords` check: if non-empty, all must be present in lowered text.
- `_ensure_telegram_post_has_signal`: uses `profile.positive_relevance_keywords` instead of `_JOB_SIGNAL_PATTERNS`, uses `profile.exclude_keywords` instead of `_IRRELEVANT_PATTERNS`.
- `_ensure_career_page_has_job_signal`: uses `profile.positive_relevance_keywords`.
- `_ensure_telegram_comment_has_signal`: uses `profile.positive_relevance_keywords`.
- Helper `_has_any` replaces `_has_pattern` (rename only, same logic).

### 8. `nodes/relevance.py`

- Remove `_POSITIVE_KEYWORDS` and `_NEGATIVE_KEYWORDS` module-level constants.
- `AIRoleRelevanceNode.__init__` signature: `def __init__(self, *, profile: FilterProfile | None = None) -> None:`
- In `__init__`: `self._profile = profile if profile is not None else FilterProfile.default()`
- Update `process()`:
  - Use `profile.negative_relevance_keywords` for negative check.
  - Use `profile.positive_relevance_keywords` for positive scoring.
  - If `positive_relevance_keywords` is empty → skip positive check, return item (passthrough).
  - `relevance_score = min(1.0, positive_hits / 3.0)`
  - Drop condition: `relevance_score <= profile.relevance_threshold` (when threshold=0.0, drops only zero-score items — identical to pre-Phase-12).

### 9. `config.py`

Add field: `filter_profile_path: Path | None = None` to `Settings`.
Also add `"filter_profile_path"` to the `strip_optional_strings` validator list? No — it's a Path, not a string. Leave as-is, pydantic handles Path coercion.

### 10. `application/pipeline.py` — `RunSummary`

Add field: `applied_profile: str | None = None` to `RunSummary` dataclass (RM-077).

### 11. `app.py`

- Add import: `from domain.filter_profile import FilterProfile`
- Add function `load_filter_profile(settings: Settings) -> FilterProfile | None`:
  - If `settings.filter_profile_path is None`: return `None`.
  - Else: `from application.filter_profile_loader import load_filter_profile as _load; return _load(settings.filter_profile_path)`
- Update `build_nodes(settings, store, llm)` signature to `build_nodes(settings, store, llm, profile: FilterProfile | None = None)`.
- Pass `profile=profile` to both `HeuristicTriageNode(profile=profile)` and `AIRoleRelevanceNode(profile=profile)`.
- In `run_pipeline()`: call `profile = load_filter_profile(settings)` before `build_nodes`.
- After `pipeline.run()`: if `profile is not None`, set `summary.applied_profile = profile.name`.

---

## Test spec (`tests/test_phase12_filter_profiles.py`)

All tests are pure unit tests (no pipeline, no I/O). Import only domain + nodes.

### Test 1: `test_default_profile_matches_hardcoded_values`
Assert `FilterProfile.default().positive_relevance_keywords` contains `"llm"`, `"ai"`, `"genai"`.
Assert `FilterProfile.default().negative_relevance_keywords` contains `"sales"`, `"recruiter"`.
Assert `FilterProfile.default().min_text_tokens == 3`.
Assert `FilterProfile.default().min_text_chars == 18`.
Assert `FilterProfile.default().relevance_threshold == 0.0`.

### Test 2: `test_triage_node_uses_profile_min_lengths`
Create `FilterProfile` with `min_text_tokens=10, min_text_chars=100`.
Construct `HeuristicTriageNode(profile=profile)`.
Pass a short `RawItem` (5 tokens, 30 chars) of kind `TELEGRAM_CHANNEL` → expect `RawItemDropped(TOO_SHORT)`.

### Test 3: `test_triage_node_default_profile_backward_compatible`
`HeuristicTriageNode()` (no profile) should drop a `TELEGRAM_CHANNEL` item with text `"webinar signup now"` as `IRRELEVANT_CONTENT`.
Should pass a `TELEGRAM_CHANNEL` item with text `"Senior ML engineer, remote, salary negotiable"`.

### Test 4: `test_triage_node_custom_exclude_drops_item`
Profile with `exclude_keywords=["sponsor"]`.
`TELEGRAM_CHANNEL` item with text `"sponsor this event and get visibility"` → dropped as `IRRELEVANT_CONTENT`.

### Test 5: `test_triage_node_allowed_source_kinds_filters`
Profile with `allowed_source_kinds=["telegram_channel"]`.
`TELEGRAM_GROUP` item → dropped as `IRRELEVANT_CONTENT`.
`TELEGRAM_CHANNEL` item with valid text → passes.

### Test 6: `test_triage_node_required_keywords`
Profile with `required_keywords=["python"]`.
Item without "python" in text → dropped.
Item with "python" in text and valid signal → passes.

### Test 7: `test_relevance_node_uses_profile_keywords`
Profile with `positive_relevance_keywords=["backend", "api"]`, `negative_relevance_keywords=["sales"]`, `relevance_threshold=0.0`.
Job with title `"Backend API Engineer"` → passes with `relevance_score > 0`.
Job with title `"Sales Manager"` → dropped as `JOB_OUT_OF_SCOPE`.

### Test 8: `test_relevance_node_empty_positive_keywords_passthrough`
Profile with `positive_relevance_keywords=[]`, `negative_relevance_keywords=[]`.
Any job → passes (relevance check disabled when no positive keywords defined).

### Test 9: `test_relevance_node_threshold`
Profile with `positive_relevance_keywords=["python"]`, `relevance_threshold=0.5`.
Job with 1 positive hit → `relevance_score = 0.333` → dropped (score <= 0.5 threshold).
Job with 2 positive hits → `relevance_score = 0.667` → passes.

### Test 10: `test_relevance_node_default_profile_backward_compatible`
`AIRoleRelevanceNode()` (no profile arg) should drop `"Marketing Director at Fortune 500"`.
Should pass `"LLM Engineer at AI startup"` with `relevance_score > 0`.

### Test 11: `test_filter_profile_immutable`
`FilterProfile.default()` is frozen — mutating a field raises `ValidationError` or `TypeError`.

### Test 12: `test_filter_profile_load_from_json`
Write a valid JSON file to tmp, load with `load_filter_profile(path)`, assert fields match.

---

## Implementation notes

1. Keep `_COMMENT_SIGNAL_PATTERNS` and `_CAREER_NAVIGATION_PATTERNS` hardcoded in `nodes/triage.py`.
   These are structural triage rules, not role-filter tuning. The roadmap says "Replaces all hardcoded
   keyword lists" — this refers to role-filtering keywords, not structural pipeline guards.

2. `relevance_threshold=0.0` in default profile: drop condition `score <= 0.0` is equivalent to
   `score == 0.0` (score is always >= 0), which mirrors the original `if relevance_score == 0.0: drop`.

3. No new PyYAML dependency needed — it's already an optional dep used lazily in `source_loader.py`.

4. The `profiles/` directory ships with the package but is NOT added to `hatch.build.targets.wheel.packages`
   — it's a data directory, not a Python package. Reference it from docs.

5. Linter: run `uv run ruff check . --fix` and `uv run ruff format .` before committing.

6. Mypy: run `uv run mypy .` and fix any type errors.

7. Tests: run `uv run pytest tests/ -v` — all existing tests must still pass.

8. Commit message: `feat(phase-12): configurable filter profiles — FilterProfile + RM-074..078`

---

## Validation checklist

- [ ] `grep -r "from infrastructure" domain/ application/ nodes/ sinks/` returns empty
- [ ] `uv run ruff check .` passes
- [ ] `uv run mypy .` passes
- [ ] `uv run pytest tests/` passes (including all pre-existing tests)
- [ ] `HeuristicTriageNode()` (no args) behaves identically to pre-Phase-12
- [ ] `AIRoleRelevanceNode()` (no args) behaves identically to pre-Phase-12
- [ ] `RunSummary.applied_profile` is `None` when no profile path is set
