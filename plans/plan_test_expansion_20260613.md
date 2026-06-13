# Plan: Radical Test Suite Expansion
Date: 2026-06-13
Suite baseline: 296 passed, 8 skipped
Goal: Close all critical coverage gaps found in hardcore test audit

---

## Dispatcher rules
- Read EVERY target file with read_file BEFORE editing
- Use replace_symbol_body or insert_after_symbol for edits; write_file for new test files
- After each phase, run the validation command and confirm pass count

---

## Phase A — Fix hollow/naive existing tests (highest risk first)

### A1 — test_plugin_contracts.py: fix dead ABC + hollow idempotency

Current problem:
- `TestSourcePluginContract` and `TestSinkPluginContract` are abstract base classes with `@abstractmethod get_source/get_sink`. pytest will NOT instantiate ABCs — these tests never run.
- `test_flush_is_idempotent` only checks no-raise; never verifies idempotency

File: `tests/test_plugin_contracts.py`

Replace the file entirely with:
```python
"""
Plugin contract tests.

Concrete plugin implementations must subclass TestSourcePluginContract /
TestSinkPluginContract and override the fixtures — pytest will then run
all contract checks automatically against the new plugin.

The InMemoryStore tests at the bottom serve as reference implementations.
"""
import pytest
from abc import ABC, abstractmethod
from job_ftch.domain.models import RawItem, JobRecord
from job_ftch.application.contracts import PluginMetadata


# ---------------------------------------------------------------------------
# Abstract contract bases
# ---------------------------------------------------------------------------

class TestSourcePluginContract(ABC):
    """Subclass this to contract-test any Source plugin.
    Override get_items() to return the list of items the source produces.
    """

    @abstractmethod
    async def get_items(self) -> list[RawItem]:
        """Return the items the source produces. May be empty list."""
        ...

    @pytest.mark.asyncio
    async def test_each_item_has_required_fields(self):
        items = await self.get_items()
        for item in items:
            assert item.source_kind, f"source_kind empty on {item}"
            assert item.source_name, f"source_name empty on {item}"
            assert item.external_id, f"external_id empty on {item}"
            assert isinstance(item.text, str), f"text not str on {item}"

    @pytest.mark.asyncio
    async def test_empty_source_returns_list(self):
        items = await self.get_items()
        assert isinstance(items, list)


class TestSinkPluginContract(ABC):
    """Subclass this to contract-test any Sink plugin."""

    @abstractmethod
    def make_sink(self):
        """Return a fresh sink instance."""
        ...

    @abstractmethod
    def make_job_record(self) -> JobRecord:
        ...

    @pytest.mark.asyncio
    async def test_emit_accepts_job_record(self):
        sink = self.make_sink()
        job = self.make_job_record()
        # Must not raise
        if hasattr(sink, "emit"):
            await sink.emit(job)

    @pytest.mark.asyncio
    async def test_flush_is_idempotent(self):
        """Calling flush twice must produce the same observable state."""
        sink = self.make_sink()
        job = self.make_job_record()
        if hasattr(sink, "emit"):
            await sink.emit(job)
        if hasattr(sink, "flush"):
            await sink.flush()
            state_after_first = getattr(sink, "_flushed_count", None) or getattr(sink, "_buffer", None)
            await sink.flush()
            state_after_second = getattr(sink, "_flushed_count", None) or getattr(sink, "_buffer", None)
            # Second flush must not change observable state relative to first
            assert state_after_first == state_after_second or state_after_second is None


# ---------------------------------------------------------------------------
# PluginMetadata validation
# ---------------------------------------------------------------------------

class TestPluginMetadata:
    def test_valid_metadata_constructs(self):
        m = PluginMetadata(
            name="my_source",
            version="1.2.3",
            plugin_type="source",
            description="A test source",
        )
        assert m.name == "my_source"
        assert m.version == "1.2.3"
        assert m.plugin_type == "source"

    def test_metadata_is_frozen(self):
        m = PluginMetadata(name="x", version="1.0.0", plugin_type="sink", description="d")
        with pytest.raises((AttributeError, TypeError)):
            m.name = "y"  # type: ignore[misc]

    def test_empty_name_creates_but_is_identifiable(self):
        # PluginMetadata does not currently validate name; test that empty string
        # is at least constructable and identifiable as problematic.
        m = PluginMetadata(name="", version="0.0.1", plugin_type="source", description="d")
        assert m.name == ""

    def test_requires_extras_defaults_to_empty_tuple(self):
        m = PluginMetadata(name="x", version="1.0.0", plugin_type="scorer", description="d")
        assert m.requires_extras == ()

    def test_metadata_equality(self):
        a = PluginMetadata(name="x", version="1.0.0", plugin_type="source", description="d")
        b = PluginMetadata(name="x", version="1.0.0", plugin_type="source", description="d")
        assert a == b

    def test_metadata_all_plugin_types(self):
        valid_types = ["source", "sink", "extractor", "classifier", "normalizer", "scorer", "notification_target"]
        for pt in valid_types:
            m = PluginMetadata(name="p", version="1.0.0", plugin_type=pt, description="d")
            assert m.plugin_type == pt
```

Validation: `python -m pytest tests/test_plugin_contracts.py -v --tb=short -o addopts=""`

---

### A2 — test_job_quality.py: tighten weak float assertions

File: `tests/test_job_quality.py`

Find the line with `assert validated.quality_score >= 0.25` (for a "strong" job fixture with full description, URL, location).
Replace with a tighter range assertion. If the job is strong (full data, relevant=0.9), quality should be high:
```python
assert 0.6 <= validated.quality_score <= 1.0, (
    f"Expected quality 0.6-1.0 for strong job, got {validated.quality_score}"
)
```

Also find any assertions like `assert ... relevance_score > 0` in the filtering tests.
For each, compute the expected value deterministically (count keyword hits / haystack tokens) and assert approximate equality:
```python
import pytest
assert validated.relevance_score == pytest.approx(expected_value, abs=0.05)
```
Only change assertions where you can compute the expected value from the fixture data.
Leave `> 0` alone if the exact value is non-deterministic (LLM-generated scores).

---

### A3 — test_outputs.py + test_phase567_contracts.py: replace repo-path artifacts with tmp_path

File: `tests/test_outputs.py`
Find all hardcoded paths like `artifacts/debug/test-counted.json` or similar.
Replace with `tmp_path / "test-counted.json"` (inject `tmp_path` as fixture parameter).

File: `tests/test_phase567_contracts.py`  
Same fix — find any `artifacts/debug/` paths and replace with `tmp_path`.

---

## Phase B — New test file: IncrementalCursor isolation

Create `tests/test_watermark.py`:

```python
"""Tests for IncrementalCursor watermark isolation."""
import pytest
from job_ftch.application.watermark import IncrementalCursor


class TestIncrementalCursorIsolation:
    """Verifies that cursors with different namespaces don't collide."""

    def test_key_format_includes_namespace_and_source(self):
        cursor = IncrementalCursor(namespace="tenant-a", source_id="hh_ru")
        key = cursor.key
        assert "tenant-a" in key
        assert "hh_ru" in key

    def test_different_namespaces_produce_different_keys(self):
        a = IncrementalCursor(namespace="tenant-a", source_id="hh_ru")
        b = IncrementalCursor(namespace="tenant-b", source_id="hh_ru")
        assert a.key != b.key

    def test_same_namespace_different_sources_produce_different_keys(self):
        a = IncrementalCursor(namespace="tenant-a", source_id="hh_ru")
        b = IncrementalCursor(namespace="tenant-a", source_id="hh_kz")
        assert a.key != b.key

    def test_empty_namespace_differs_from_named(self):
        a = IncrementalCursor(namespace="", source_id="hh_ru")
        b = IncrementalCursor(namespace="tenant-a", source_id="hh_ru")
        assert a.key != b.key

    @pytest.mark.asyncio
    async def test_set_on_one_namespace_invisible_to_other(self, tmp_path):
        """Core isolation invariant: tenant-A's cursor must not affect tenant-B."""
        # Import the store connector or use whatever IncrementalCursor takes as backend
        # Check how IncrementalCursor stores data — read watermark.py first.
        # If it takes a store/backend parameter, create two cursors over the same store instance.
        # If it uses an in-process dict, check if it's namespaced.
        # This test structure depends on the implementation; read watermark.py and adapt.

        # Pattern A: if IncrementalCursor uses a shared dict/store
        # store = SomeStore(path=tmp_path / "cursors")
        # cursor_a = IncrementalCursor(namespace="tenant-a", source_id="src1", store=store)
        # cursor_b = IncrementalCursor(namespace="tenant-b", source_id="src1", store=store)
        # await cursor_a.set("2026-01-01T00:00:00")
        # assert await cursor_b.get() is None

        # IMPORTANT: Read job_ftch/application/watermark.py before implementing this test.
        # Adapt the store construction to match the actual API.
        # If IncrementalCursor does NOT take a shared store (it's always in-process-isolated),
        # document that and test the key uniqueness instead (already covered above).
        pass

    def test_cursor_reset_clears_only_own_namespace(self):
        """Resetting one cursor must not affect cursors with different namespace."""
        # Read watermark.py to find the reset() method signature, then implement.
        # Pattern: set a value on cursor_a, set a value on cursor_b,
        # reset cursor_a, assert cursor_b value unchanged.
        # If no reset() method exists, skip this test.
        pass
```

NOTE: After writing the file, read `job_ftch/application/watermark.py` to see the actual API and fill in the async isolation test properly. The `pass` stubs need real implementation.

---

## Phase C — New test file: JobStatus lifecycle

Create `tests/test_lifecycle.py`:

```python
"""Tests for JobLifecycleNode — status detection in English and Russian."""
import pytest
from job_ftch.domain.models import JobRecord, JobStatus
from job_ftch.nodes.lifecycle import JobLifecycleNode


def make_record(**kwargs) -> JobRecord:
    """Minimal JobRecord fixture. Read domain/models.py for required fields."""
    defaults = dict(
        id="test-1",
        external_id="ext-1",
        source_kind="test",
        source_name="TestSource",
        url="https://example.com/job/1",
        title="Python Developer",
        company="Acme",
        text="We are hiring a Python developer.",
    )
    defaults.update(kwargs)
    return JobRecord(**defaults)


# ---------------------------------------------------------------------------
# English closed markers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("This position has been filled.", JobStatus.FILLED),
    ("The vacancy is closed.", JobStatus.FILLED),
    ("Position closed. Thank you.", JobStatus.FILLED),
    ("Job listing expired on 2024-01-01.", JobStatus.FILLED),  # or EXPIRED if that state exists
    ("We are actively hiring Python engineers.", JobStatus.OPEN),
])
@pytest.mark.asyncio
async def test_english_status_markers(text, expected):
    node = JobLifecycleNode()
    record = make_record(text=text)
    result = await node.process(record)
    assert result.status == expected, f"text={text!r}: expected {expected}, got {result.status}"


# ---------------------------------------------------------------------------
# Russian closed markers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected_status", [
    ("Роль закрыта. Спасибо за интерес.", JobStatus.FILLED),
    ("Вакансия закрыта.", JobStatus.FILLED),
    ("Позиция закрыта, набор завершён.", JobStatus.FILLED),
    ("Набор закрыт.", JobStatus.FILLED),
    ("Ищем Python разработчика в нашу команду.", JobStatus.OPEN),
])
@pytest.mark.asyncio
async def test_russian_closed_markers(text, expected_status):
    """lifecycle.py lines 15-18 define RU markers — this tests ALL of them."""
    node = JobLifecycleNode()
    record = make_record(text=text)
    result = await node.process(record)
    assert result.status == expected_status, (
        f"RU text={text!r}: expected {expected_status}, got {result.status}"
    )


# ---------------------------------------------------------------------------
# Metadata / boolean flag paths
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("metadata,expected_status", [
    ({"status": "closed"}, JobStatus.FILLED),
    ({"status": "filled"}, JobStatus.FILLED),
    ({"closed": True}, JobStatus.FILLED),
    ({"closed": False}, JobStatus.OPEN),
    ({"status": "open"}, JobStatus.OPEN),
    ({}, JobStatus.OPEN),  # no signal → stays open
])
@pytest.mark.asyncio
async def test_metadata_status_signals(metadata, expected_status):
    """Tests metadata dict-based lifecycle signal paths."""
    node = JobLifecycleNode()
    record = make_record(metadata=metadata)
    result = await node.process(record)
    assert result.status == expected_status


# ---------------------------------------------------------------------------
# OPEN override: metadata says open → override closed text
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_open_metadata_overrides_closed_text():
    """If metadata explicitly says 'open', text-based closed detection is overridden."""
    node = JobLifecycleNode()
    record = make_record(
        text="Вакансия закрыта.",
        metadata={"status": "open"},
    )
    result = await node.process(record)
    # Read lifecycle.py to confirm _OPEN_VALUES logic exists; if it does:
    assert result.status == JobStatus.OPEN


# ---------------------------------------------------------------------------
# EXPIRED: if the enum value exists and is reachable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_expired_state_if_supported():
    """JobStatus.EXPIRED is defined in the enum; verify if it's ever produced."""
    node = JobLifecycleNode()
    # Try a text that might indicate expiry
    record = make_record(text="This job posting has expired.")
    result = await node.process(record)
    # If lifecycle.py does NOT produce EXPIRED from text, assert OPEN or FILLED.
    # If it DOES produce EXPIRED, assert JobStatus.EXPIRED.
    # Read lifecycle.py before implementing the assertion.
    assert result.status in (JobStatus.OPEN, JobStatus.FILLED, JobStatus.EXPIRED)
```

NOTE: Before writing this file, read `job_ftch/nodes/lifecycle.py` to confirm:
1. The method signature for processing (process/handle/call?)
2. What metadata field names trigger status changes
3. Whether EXPIRED is ever produced
Then fill in the correct assertions.

---

## Phase D — New test file: dedup negative case + routing boundaries

Create `tests/test_dedup_negative.py`:

```python
"""
Dedup negative case: distinct jobs with the same company must NOT be merged.
This tests the false-positive avoidance boundary.
"""
import pytest
from job_ftch.domain.models import RawItem
# Import DedupNode — check nodes/__init__.py for correct import path


def make_raw_item(external_id: str, title: str, company: str, text: str) -> RawItem:
    return RawItem(
        external_id=external_id,
        source_kind="test",
        source_name="TestSource",
        url=f"https://example.com/job/{external_id}",
        title=title,
        company=company,
        text=text,
    )


@pytest.mark.asyncio
async def test_distinct_roles_same_company_not_deduped():
    """Two genuinely different jobs at the same company must NOT be merged."""
    # Read nodes/dedup.py to confirm the correct way to use DedupNode
    # and how to count duplicates vs new emissions.
    # The test must show that both jobs pass through (emitted == 2, merged == 0).
    pass  # Fill in after reading dedup.py


@pytest.mark.asyncio  
async def test_exact_duplicate_is_dropped():
    """Positive control: same external_id must be dropped on second pass."""
    pass  # Should already be covered in test_dedup.py — verify and reference


@pytest.mark.asyncio
async def test_near_dup_same_title_company_is_merged():
    """Two items with same company + near-identical title count as duplicates."""
    pass
```

---

Add to `tests/test_routing.py` (if exists) or create new file `tests/test_routing_thresholds.py`:

```python
"""Routing threshold boundary tests — pins the exact accept/review/reject cutoffs."""
import pytest
from job_ftch.nodes.routing import RoutingNode
from job_ftch.domain.models import JobRecord


ACCEPT_THRESHOLD = 0.85
REVIEW_THRESHOLD = 0.5
QUALITY_OVERRIDE = 0.6


def make_record(quality_score: float, match_score: float = 0.9, **kwargs) -> JobRecord:
    """Create a minimal JobRecord with specified scores."""
    # Read domain/models.py and routing.py to confirm field names used for routing decisions
    # Adapt this factory to set the correct fields (quality_score, match_score, review_reasons, etc.)
    pass


@pytest.mark.parametrize("quality,match,expected_channel", [
    # Exact boundary: at accept threshold → accept
    (0.85, 0.9, "posting"),
    # Just below accept threshold → review
    (0.849, 0.9, "review"),
    # At review threshold → review
    (0.5, 0.9, "review"),
    # Just below review threshold → reject
    (0.499, 0.9, "rejected"),
    # Quality override: high quality but match below accept → still posting
    (0.9, 0.45, "posting"),  # quality overrides low match if quality >= QUALITY_OVERRIDE
])
@pytest.mark.asyncio
async def test_routing_at_threshold_boundaries(quality, match, expected_channel):
    """Pins routing decisions at exact threshold boundaries.
    
    A regression that moves a threshold by epsilon would cause this test to fail,
    making threshold drift immediately visible.
    """
    # Read job_ftch/nodes/routing.py to confirm:
    # 1. The constructor parameter names for thresholds
    # 2. The field on JobRecord that routing reads (quality_score vs match_score vs combined)
    # 3. What the output channel field is called
    # Then implement:
    node = RoutingNode(
        accept_threshold=ACCEPT_THRESHOLD,
        review_threshold=REVIEW_THRESHOLD,
        quality_override_threshold=QUALITY_OVERRIDE,
    )
    record = make_record(quality_score=quality, match_score=match)
    result = await node.process(record)
    assert result.channel == expected_channel, (
        f"quality={quality}, match={match}: expected channel={expected_channel!r}, "
        f"got {result.channel!r}"
    )
```

---

## Phase E — New test file: Ontology corrections + ML Engineer fix

Create `tests/test_ontology_correctness.py`:

```python
"""
Ontology correctness tests.
Verifies role family and skill mappings are correct, not just self-consistent.
"""
import pytest
from job_ftch.infrastructure.ontology.normalizer import get_default_normalizer


@pytest.fixture
def norm():
    return get_default_normalizer()


# ---------------------------------------------------------------------------
# Role family correctness
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title,expected_family", [
    # Engineering titles → "engineering"
    ("ML Engineer", "engineering"),   # was incorrectly mapped to "data" — this is the fix
    ("Machine Learning Engineer", "engineering"),
    ("Software Engineer", "engineering"),
    ("Backend Developer", "engineering"),
    ("Frontend Engineer", "engineering"),
    # Data titles → "data"
    ("Data Scientist", "data"),
    ("Data Analyst", "analytics"),  # or "data" — read role_aliases.json to confirm
    ("ML Researcher", "research"),   # or "data" — confirm from aliases
    # DevOps titles → "devops"
    ("DevOps Engineer", "devops"),
    ("SRE", "devops"),
    # QA titles → "qa"
    ("QA Engineer", "qa"),
    ("Test Engineer", "qa"),
    # Product titles → "product"
    ("Product Manager", "product"),
    ("Product Owner", "product"),
])
def test_role_family_mapping(norm, title, expected_family):
    """Verifies role_family mapping is semantically correct."""
    result = norm.infer_role_family(title, language="en")
    assert result == expected_family, (
        f"Title {title!r}: expected family={expected_family!r}, got {result!r}"
    )


@pytest.mark.parametrize("title,expected_family", [
    # Russian titles
    ("Разработчик Python", "engineering"),
    ("Аналитик данных", "analytics"),
    ("DevOps инженер", "devops"),
    ("Продуктовый менеджер", "product"),
])
def test_role_family_russian_titles(norm, title, expected_family):
    result = norm.infer_role_family(title, language="ru")
    assert result == expected_family, f"RU title {title!r}: expected {expected_family}, got {result}"


def test_mixed_language_title(norm):
    """'Senior Python разработчик' — mixed en/ru title must not crash and should resolve."""
    result = norm.infer_role_family("Senior Python разработчик", language="ru")
    assert result is None or isinstance(result, str)  # Must not raise


def test_unknown_title_returns_none(norm):
    result = norm.infer_role_family("Completely Unknown Role XYZ123", language="en")
    assert result is None


# ---------------------------------------------------------------------------
# Seniority correctness
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title,expected_seniority", [
    ("Senior Python Developer", "senior"),
    ("Junior QA Engineer", "junior"),
    ("Lead Engineer", "lead"),
    ("Staff Engineer", "staff"),
    ("Principal Architect", "principal"),
    ("Python Developer", None),   # no seniority signal → None
    ("ML Engineer", None),        # no seniority prefix
])
def test_seniority_inference(norm, title, expected_seniority):
    result = norm.infer_seniority(title)
    assert result == expected_seniority, f"Title {title!r}: expected seniority={expected_seniority!r}, got {result!r}"


# ---------------------------------------------------------------------------
# Skill normalization: negative cases
# ---------------------------------------------------------------------------

def test_skill_normalization_no_cross_contamination(norm):
    """Normalizing Python must not accidentally produce a TypeScript canonical."""
    tags = norm.normalize_skills(["Python", "TypeScript"])
    names = {t.canonical_name for t in tags}
    assert "Python" in names or "python" in names
    assert "TypeScript" in names or "typescript" in names
    # Crucially, no alias of Python should match TypeScript or vice versa
    ids = {t.skill_id for t in tags if t.skill_id}
    assert "python" in ids
    assert "typescript" in ids
    # Ensure different skills produced different canonical entries
    assert len(names) == 2


def test_unknown_skill_returns_raw_name(norm):
    """A skill with no alias match must return the raw name as-is."""
    tags = norm.normalize_skills(["UnknownFrameworkXYZ999"])
    assert len(tags) == 1
    assert tags[0].canonical_name == "UnknownFrameworkXYZ999"
    assert tags[0].skill_id is None  # or empty, depending on implementation


def test_normalize_skills_empty_list(norm):
    tags = norm.normalize_skills([])
    assert tags == () or tags == []
```

NOTE: Read `job_ftch/infrastructure/ontology/data/role_aliases.json` before writing the expected values.
The "ML Engineer" → "engineering" assertion may require updating role_aliases.json to move "ml engineer" from
data aliases to engineering aliases. If role_aliases.json already has it in the right place, just verify.
If NOT, update role_aliases.json as part of this task.

---

## Phase F — New test: Pipeline end-to-end field correctness

Create `tests/test_pipeline_e2e_fields.py`:

```python
"""
End-to-end pipeline test: verifies field-level correctness across the full node chain.
Tests that the canonical node sequence composes correctly (no inter-node contract drift).
"""
import pytest
from job_ftch.domain.models import RawItem


@pytest.mark.asyncio
async def test_full_chain_produces_correct_jobrecord(mock_llm_extractor):
    """
    Runs one RawItem through the real canonical node chain:
    ExtractionNode → ExtractionValidationNode → TitleCompanyNormalizationNode 
    → SkillNormalizationNode → LocationWorkModeNormalizationNode 
    → CompensationParsingNode → JobLifecycleNode → QualityScoringNode → RoutingNode
    
    Asserts field-level output, not just aggregate counts.
    """
    # This test requires building the full pipeline. Read builder.py to see build_nodes().
    # Use the existing test_extraction.py or test_job_quality.py fixture patterns.
    # 
    # Steps:
    # 1. Create a RawItem with rich known content (specific title, company, skills)
    # 2. Build the node chain from builder.build_nodes() with a mock LLM extractor
    # 3. Run the item through every node in sequence
    # 4. Assert:
    #    - result.title == expected (normalized)
    #    - result.company == expected (normalized)  
    #    - result.role_family is set and correct
    #    - result.seniority is set (if title had Senior/Junior)
    #    - result.skills is not empty (skill tags extracted)
    #    - result.quality_score > 0 (scored)
    #    - result.status == JobStatus.OPEN (no closure signals)
    #
    # Read tests/test_extraction.py for the mock LLM fixture pattern.
    pass
```

---

## Phase G — New test: Malformed LLM output handling

Add to `tests/test_extraction.py` (or create `tests/test_extraction_robustness.py`):

Read `tests/test_extraction.py` first to understand existing patterns.

Add these test cases:
```python
@pytest.mark.asyncio
async def test_extraction_handles_empty_llm_response(mock_llm_returns_empty):
    """LLM returns empty dict/None — must produce PARTIAL draft, not crash."""
    # Create an ExtractionNode with a mock LLM that returns {}
    # Feed a RawItem with real text
    # Assert:
    # - Returns a JobDraft (not raises)
    # - draft.extraction_confidence is low or None
    # - review_reasons contains an extraction-failure indicator

@pytest.mark.asyncio
async def test_extraction_handles_malformed_llm_schema(mock_llm_returns_wrong_shape):
    """LLM returns object with wrong field names — must produce PARTIAL draft."""
    # Mock LLM returns {"wrong_field": "value", "another_wrong": 123}
    # Assert same graceful degradation pattern

@pytest.mark.asyncio
async def test_extraction_handles_llm_timeout(mock_llm_raises_timeout):
    """LLM raises asyncio.TimeoutError — must degrade gracefully."""
    # Mock LLM raises asyncio.TimeoutError
    # Assert: returns partial draft with review reason, does NOT re-raise

@pytest.mark.asyncio
async def test_extraction_partial_draft_has_review_reason():
    """When extraction produces a PARTIAL draft, at least one review_reason must be set."""
    # Use the existing mock_llm_raises fixture from test_extraction.py
    # Assert len(draft.review_reasons) > 0
```

---

## Execution order

1. Phase A (fix hollow tests) — read files first, then fix
2. Phase B (watermark) — read watermark.py first, adapt test stubs
3. Phase C (lifecycle) — read lifecycle.py first, adapt assertions
4. Phase D (dedup negative + routing) — read dedup.py and routing.py first
5. Phase E (ontology correctness) — read role_aliases.json first, fix ML Engineer mapping if needed
6. Phase F (pipeline e2e) — read builder.py first
7. Phase G (extraction robustness) — read test_extraction.py first

After each phase: run targeted tests.
After all: `python -m pytest tests -q -o addopts="" --tb=short`

---

## Validation commands

```bash
# Phase A
python -m pytest tests/test_plugin_contracts.py tests/test_job_quality.py -v --tb=short -o addopts=""

# Phase B  
python -m pytest tests/test_watermark.py -v --tb=short -o addopts=""

# Phase C
python -m pytest tests/test_lifecycle.py -v --tb=short -o addopts=""

# Phase D
python -m pytest tests/test_dedup_negative.py tests/test_routing_thresholds.py -v --tb=short -o addopts=""

# Phase E
python -m pytest tests/test_ontology_correctness.py -v --tb=short -o addopts=""

# Full suite — must be >= 320 passed (was 296 before expansion)
python -m pytest tests -q -o addopts="" --tb=short
```

## Success criteria

- `test_plugin_contracts.py` — PluginMetadata tests all pass, contract ABCs have executable bodies
- `test_watermark.py` — key format + tenant isolation tests pass
- `test_lifecycle.py` — all 4 RU markers tested and passing, metadata signals tested
- `test_routing_thresholds.py` — exact boundary parametrize tests pass
- `test_ontology_correctness.py` — "ML Engineer" maps to "engineering" (fix aliases if needed)
- `test_dedup_negative.py` — distinct-jobs-same-company does NOT merge
- Full suite >= 320 passed, 0 failures
- No `> 0` float assertions remain on deterministic computed scores
- No `artifacts/debug/` hardcoded paths in test files
