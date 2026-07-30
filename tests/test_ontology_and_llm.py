"""Tests for the new ontology stores and LLM touchpoints (ADR-029, ADR-030)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from job_ftch.application.profile_inputs import (
    add_example_to_profile_with_enrichment,
    build_profile_from_resume_text,
)
from job_ftch.application.run_budget import AsyncCallBudget
from job_ftch.domain import (
    CompiledOntology,
    CompiledOntologyTerm,
    JobRecord,
    LanguageCode,
    MatchDecision,
    PresentableJob,
    RelevanceClassification,
    RelevanceEvidenceClassification,
)
from job_ftch.domain.models import WorkMode
from job_ftch.infrastructure.ontology.db_store import DBOntologyStore
from job_ftch.infrastructure.ontology.file_store import FileOntologyStore

if TYPE_CHECKING:
    from job_ftch.application.contracts import OntologyStore

# ----- FileOntologyStore tests -----


@pytest.mark.asyncio
async def test_file_ontology_store_lifecycle() -> None:
    with tempfile.TemporaryDirectory() as td:
        store: OntologyStore = FileOntologyStore(Path(td))
        # Empty
        assert await store.list_skills() == ()
        assert await store.list_roles() == ()
        assert await store.list_seniority() == ()
        assert await store.list_anti_patterns() == ()
        assert await store.list_positive_keywords() == ()
        assert await store.list_negative_keywords() == ()

        await store.upsert_skill("python", lang="en")
        await store.upsert_skill("pytorch", alias="torch", lang="en")
        assert "python" in await store.list_skills()
        assert "pytorch" in await store.list_skills()

        # Lookup
        assert await store.lookup_skill("Python") == "python"
        assert await store.lookup_skill("torch") == "pytorch"
        assert await store.lookup_skill("unknown") is None

        await store.upsert_role("data scientist", lang="en")
        assert "data scientist" in await store.list_roles()

        await store.upsert_seniority("middle")
        await store.upsert_seniority("senior")
        assert "middle" in await store.list_seniority()

        await store.upsert_anti_pattern("cobol")
        assert "cobol" in await store.list_anti_patterns()
        await store.upsert_positive_keyword("llm", weight=5)
        await store.upsert_negative_keyword("etl only", weight=3)
        assert {"term": "llm", "weight": 5} in await store.list_positive_keywords()
        assert {"term": "etl only", "weight": 3} in await store.list_negative_keywords()


# ----- DBOntologyStore tests -----


@pytest.mark.asyncio
async def test_db_ontology_store_lifecycle(tmp_path: Path) -> None:
    db_path = tmp_path / "test_ontology.db"
    store: OntologyStore = DBOntologyStore(db_path)
    await store.upsert_skill("python", lang="en")
    await store.upsert_skill("pytorch", alias="torch", lang="en")
    skills = await store.list_skills()
    assert "python" in skills
    assert "pytorch" in skills

    # Lookup
    assert await store.lookup_skill("torch") == "pytorch"
    assert await store.lookup_skill("Python") == "python"

    # Persists across reopens
    await store.close()
    store2: OntologyStore = DBOntologyStore(db_path)
    skills2 = await store2.list_skills()
    assert "python" in skills2
    assert "pytorch" in skills2
    await store2.upsert_positive_keyword("llm", weight=5)
    await store2.upsert_negative_keyword("etl only", weight=2)
    assert {"term": "llm", "weight": 5} in await store2.list_positive_keywords()
    assert {"term": "etl only", "weight": 2} in await store2.list_negative_keywords()
    await store2.close()


@pytest.mark.asyncio
async def test_db_ontology_last_write_wins_for_skill_polarity(tmp_path: Path) -> None:
    store = DBOntologyStore(tmp_path / "occurrences.db")
    await store.upsert_skill(
        "python",
        polarity="positive",
        source_shot_id="positive-shot",
        prompt_hash="prompt-positive",
    )
    await store.upsert_skill(
        "python",
        polarity="negative",
        source_shot_id="negative-shot",
        prompt_hash="prompt-negative",
    )
    assert "python" not in await store.list_skills()
    assert "python" in await store.list_negative_skills()
    await store.close()


@pytest.mark.asyncio
async def test_db_ontology_persists_shot_graph(tmp_path: Path) -> None:
    from job_ftch.application.ontology_graph_builder import (
        build_ontology_graph_from_compiled,
    )

    store = DBOntologyStore(tmp_path / "graph.db")
    ontology = CompiledOntology(
        terms=(
            CompiledOntologyTerm(
                canonical="target role",
                entity_type="role",
                semantic_role="target_role",
                polarity="positive",
                scope="target",
                evidence_shot_ids=("shot-1",),
                accepted=True,
                confidence=0.9,
                weight=0.8,
            ),
        ),
    )
    graph = build_ontology_graph_from_compiled(
        ontology=ontology,
        graph_id="compiled:test",
        shot_id="shot-1",
        lang="en",
    )
    await store.upsert_shot_graph(graph)
    conn = await store._ensure_initialized()
    async with conn.execute("SELECT COUNT(*) FROM jf_ontology_graph_version") as cursor:
        assert (await cursor.fetchone())[0] == 1
    async with conn.execute("SELECT COUNT(*) FROM jf_ontology_edge") as cursor:
        assert (await cursor.fetchone())[0] >= 1
    await store.close()


@pytest.mark.asyncio
async def test_file_ontology_keyword_polarity_is_exclusive() -> None:
    with tempfile.TemporaryDirectory() as td:
        onto: OntologyStore = FileOntologyStore(Path(td))
        await onto.upsert_positive_keyword("rag", weight=4)
        await onto.upsert_negative_keyword("rag", weight=1)
        assert {"term": "rag", "weight": 1} in await onto.list_negative_keywords()
        assert {"term": "rag", "weight": 4} not in await onto.list_positive_keywords()


# ----- ShotExtraction (LLM point ①) tests -----


class _FakeLLM:
    def __init__(self, ontology: CompiledOntology) -> None:
        self._ontology = ontology
        self.calls = 0

    async def extract(self, text: str, schema):  # noqa: ARG002
        self.calls += 1
        return self._ontology

    async def classify(self, prompt, schema):  # noqa: ARG002
        self.calls += 1
        if schema.__name__ == "OntologyCandidateChunk":
            return schema(
                terms=self._ontology.terms,
                relations=self._ontology.relations,
            )
        return self._ontology

    async def present(self, job_payload, schema):  # noqa: ARG002
        self.calls += 1
        return self._ontology


class _FakeStore:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    async def get_run_state(self, key: str):
        return self.kv.get(key)

    async def set_run_state(self, key: str, value: str) -> None:
        self.kv[key] = value

    # Store protocol stubs (not used in these tests)
    async def has_processed(self, item_id: str) -> bool:  # noqa: ARG002
        return False

    async def mark_processed(self, item_id: str) -> None: ...

    async def has_dedup_key(self, key: str) -> bool:  # noqa: ARG002
        return False

    async def remember_dedup_key(self, record) -> None: ...

    async def list_dedup_keys(self, kind=None):  # noqa: ARG002
        return ()

    async def record_duplicate(self, record) -> None: ...

    async def list_duplicate_records(self):  # noqa: ARG002
        return ()

    async def get_run_state_batch(self, keys):  # noqa: ARG002
        return {k: self.kv.get(k) for k in keys}

    async def increment_metric(self, key: str, value: int = 1) -> None: ...

    async def get_metrics(self) -> dict:  # noqa: ARG002
        return {}


@pytest.mark.asyncio
async def test_add_example_with_enrichment_updates_ontology() -> None:
    with tempfile.TemporaryDirectory() as td:
        onto: OntologyStore = FileOntologyStore(Path(td))
        llm = _FakeLLM(
            CompiledOntology(
                terms=(
                    CompiledOntologyTerm(
                        canonical="rag",
                        entity_type="skill",
                        semantic_role="supporting_skill",
                        polarity="positive",
                        scope="supporting",
                        evidence_shot_ids=("shot",),
                        accepted=True,
                        confidence=0.9,
                        weight=0.8,
                    ),
                    CompiledOntologyTerm(
                        canonical="ai product engineer",
                        entity_type="role",
                        semantic_role="target_role",
                        polarity="positive",
                        scope="target",
                        evidence_shot_ids=("shot",),
                        accepted=True,
                        confidence=0.9,
                        weight=0.9,
                    ),
                    CompiledOntologyTerm(
                        canonical="senior",
                        entity_type="seniority",
                        semantic_role="seniority",
                        polarity="positive",
                        scope="target",
                        evidence_shot_ids=("shot",),
                        accepted=True,
                        confidence=0.8,
                        weight=0.6,
                    ),
                    CompiledOntologyTerm(
                        canonical="llm",
                        entity_type="keyword",
                        semantic_role="positive_keyword",
                        polarity="positive",
                        scope="target",
                        evidence_shot_ids=("shot",),
                        accepted=True,
                        confidence=1.0,
                        weight=1.0,
                    ),
                )
            )
        )
        profile = build_profile_from_resume_text("Test", user_id="u1", profile_id="p1")
        profile = await add_example_to_profile_with_enrichment(
            profile,
            "Senior Python Developer",
            kind="positive_resume",
            llm=llm,
            ontology_store=onto,
        )
        assert llm.calls == 3
        skills = await onto.list_skills()
        assert "rag" in skills
        assert "python" not in skills
        assert "fastapi" not in skills
        assert "senior" in await onto.list_seniority()
        roles = await onto.list_roles()
        assert "ai product engineer" in roles
        assert "developer" not in roles
        assert {"term": "llm", "weight": 5} in await onto.list_positive_keywords()


@pytest.mark.asyncio
async def test_add_example_negative_shot_populates_anti() -> None:
    with tempfile.TemporaryDirectory() as td:
        onto: OntologyStore = FileOntologyStore(Path(td))
        llm = _FakeLLM(
            CompiledOntology(
                terms=(
                    CompiledOntologyTerm(
                        canonical="cobol",
                        entity_type="skill",
                        semantic_role="anti_skill",
                        polarity="negative",
                        scope="anti",
                        evidence_shot_ids=("shot",),
                        accepted=True,
                        confidence=0.9,
                        weight=0.8,
                    ),
                    CompiledOntologyTerm(
                        canonical="mainframe dev",
                        entity_type="role",
                        semantic_role="anti_role",
                        polarity="negative",
                        scope="anti",
                        evidence_shot_ids=("shot",),
                        accepted=True,
                        confidence=0.9,
                        weight=0.8,
                    ),
                    CompiledOntologyTerm(
                        canonical="COBOL only",
                        entity_type="anti_pattern",
                        semantic_role="anti_pattern",
                        polarity="negative",
                        scope="anti",
                        evidence_shot_ids=("shot",),
                        accepted=True,
                        confidence=0.9,
                        weight=0.8,
                    ),
                    CompiledOntologyTerm(
                        canonical="mainframe",
                        entity_type="anti_pattern",
                        semantic_role="anti_pattern",
                        polarity="negative",
                        scope="anti",
                        evidence_shot_ids=("shot",),
                        accepted=True,
                        confidence=0.8,
                        weight=0.8,
                    ),
                    CompiledOntologyTerm(
                        canonical="mainframe",
                        entity_type="keyword",
                        semantic_role="negative_keyword",
                        polarity="negative",
                        scope="anti",
                        evidence_shot_ids=("shot",),
                        accepted=True,
                        confidence=0.8,
                        weight=0.8,
                    ),
                )
            )
        )
        profile = build_profile_from_resume_text("Test", user_id="u1", profile_id="p1")
        profile = await add_example_to_profile_with_enrichment(
            profile, "COBOL mainframe", kind="negative_job", llm=llm, ontology_store=onto
        )
        anti = await onto.list_anti_patterns()
        assert "cobol only" in anti
        assert "mainframe" in anti
        assert {"term": "mainframe", "weight": 4} in await onto.list_negative_keywords()
        # Negative roles/skills go to negative storage, NOT positive
        neg_roles = await onto.list_negative_roles()
        assert "mainframe dev" in neg_roles
        pos_roles = await onto.list_roles()
        assert "mainframe dev" not in pos_roles
        neg_skills = await onto.list_negative_skills()
        assert "cobol" in neg_skills
        pos_skills = await onto.list_skills()
        assert "cobol" not in pos_skills


@pytest.mark.asyncio
async def test_positive_shot_drops_generic_base_roles() -> None:
    """Compiler decisions, not code lists, decide target vs background roles."""
    with tempfile.TemporaryDirectory() as td:
        onto: OntologyStore = FileOntologyStore(Path(td))
        llm = _FakeLLM(
            CompiledOntology(
                terms=(
                    CompiledOntologyTerm(
                        canonical="ai project manager",
                        entity_type="role",
                        semantic_role="target_role",
                        polarity="positive",
                        scope="target",
                        evidence_shot_ids=("shot",),
                        accepted=True,
                        confidence=0.9,
                        weight=0.9,
                    ),
                    CompiledOntologyTerm(
                        canonical="project manager",
                        entity_type="role",
                        semantic_role="past_role",
                        polarity="contextual",
                        scope="past",
                        evidence_shot_ids=("shot",),
                        accepted=False,
                        reject_reason="not current or desired",
                    ),
                    CompiledOntologyTerm(
                        canonical="delivery manager",
                        entity_type="role",
                        semantic_role="context",
                        polarity="contextual",
                        scope="background",
                        evidence_shot_ids=("shot",),
                        accepted=False,
                        reject_reason="background role",
                    ),
                )
            )
        )
        profile = build_profile_from_resume_text("Test", user_id="u1", profile_id="p1")
        text = "AI Project Manager с опытом LLM-интеграций и agentic workflow"
        profile = await add_example_to_profile_with_enrichment(
            profile, text, kind="positive_resume", llm=llm, ontology_store=onto
        )
        roles = await onto.list_roles()
        assert "ai project manager" in roles
        assert "project manager" not in roles
        assert "delivery manager" not in roles


@pytest.mark.asyncio
async def test_water_terms_filtered_from_keywords_and_skills() -> None:
    """Background terms are omitted only when the compiler marks them background."""
    with tempfile.TemporaryDirectory() as td:
        onto: OntologyStore = FileOntologyStore(Path(td))
        llm = _FakeLLM(
            CompiledOntology(
                terms=(
                    CompiledOntologyTerm(
                        canonical="langchain",
                        entity_type="skill",
                        semantic_role="supporting_skill",
                        polarity="positive",
                        scope="supporting",
                        evidence_shot_ids=("shot",),
                        accepted=True,
                        confidence=0.9,
                        weight=0.8,
                    ),
                    CompiledOntologyTerm(
                        canonical="api",
                        entity_type="skill",
                        semantic_role="background_skill",
                        polarity="contextual",
                        scope="background",
                        evidence_shot_ids=("shot",),
                        accepted=True,
                    ),
                    CompiledOntologyTerm(
                        canonical="llm",
                        entity_type="keyword",
                        semantic_role="positive_keyword",
                        polarity="positive",
                        scope="target",
                        evidence_shot_ids=("shot",),
                        accepted=True,
                        confidence=1.0,
                        weight=1.0,
                    ),
                    CompiledOntologyTerm(
                        canonical="roadmap",
                        entity_type="keyword",
                        semantic_role="context",
                        polarity="contextual",
                        scope="background",
                        evidence_shot_ids=("shot",),
                        accepted=False,
                    ),
                )
            )
        )
        profile = build_profile_from_resume_text("Test", user_id="u1", profile_id="p1")
        profile = await add_example_to_profile_with_enrichment(
            profile, "AI Developer", kind="positive_resume", llm=llm, ontology_store=onto
        )
        skills = await onto.list_skills()
        assert "langchain" in skills
        assert "python" not in skills
        assert "api" not in skills
        assert "planning" not in skills
        keywords = await onto.list_positive_keywords()
        terms = [k["term"] for k in keywords]
        assert "llm" in terms
        assert "roadmap" not in terms
        assert "hiring" not in terms


@pytest.mark.asyncio
async def test_provenance_persisted_in_db_store(tmp_path: Path) -> None:
    """Provenance fields (source_shot_id, polarity, model) are stored and queryable."""
    store: OntologyStore = DBOntologyStore(tmp_path / "prov.db")
    await store.upsert_skill(
        "pytorch",
        lang="en",
        source_shot_id="abc123",
        source_type="resume",
        polarity="positive",
        model="gpt-5.4-nano",
        prompt_hash="hash1",
    )
    await store.upsert_role(
        "data scientist",
        lang="en",
        source_shot_id="def456",
        source_type="vacancy",
        polarity="negative",
        model="gpt-5.4-nano",
        prompt_hash="hash2",
    )
    pos_skills = await store.list_skills()
    assert "pytorch" in pos_skills
    neg_roles = await store.list_negative_roles()
    assert "data scientist" in neg_roles
    pos_roles = await store.list_roles()
    assert "data scientist" not in pos_roles
    await store.close()


@pytest.mark.asyncio
async def test_add_example_with_failing_llm_does_not_crash() -> None:
    with tempfile.TemporaryDirectory() as td:
        onto: OntologyStore = FileOntologyStore(Path(td))

        class _Boom:
            async def classify(self, *a, **k):
                raise RuntimeError("LLM down")

        profile = build_profile_from_resume_text("Test", user_id="u1", profile_id="p1")
        # Should not raise
        profile = await add_example_to_profile_with_enrichment(
            profile, "Anything", kind="positive_resume", llm=_Boom(), ontology_store=onto
        )
        # Ontology untouched
        assert await onto.list_skills() == ()


# ----- HeuristicLLMProvider tests -----


@pytest.mark.asyncio
async def test_heuristic_classifies_compact_relevance_schema() -> None:
    from job_ftch.infrastructure.llm.heuristic import HeuristicLLMProvider

    result = await HeuristicLLMProvider().classify(
        "## VACANCY EVIDENCE\n[1] title: LLM Engineer\n"
        "[2] responsibility: Build LLM integrations.\n\n## TASK\nReturn structured fields.",
        RelevanceEvidenceClassification,
    )

    assert result == RelevanceEvidenceClassification(
        is_job="yes",
        role_relation="target",
        responsibility_fit="support",
        positive_evidence_ids=(1,),
    )


def test_openai_provider_uses_reasoning_model_token_limit_param() -> None:
    from job_ftch.infrastructure.llm.openai_provider import _completion_token_limit_kwargs

    assert _completion_token_limit_kwargs("gpt-5.4-nano", 123) == {"max_completion_tokens": 123}
    assert _completion_token_limit_kwargs("o4-mini", 123) == {"max_completion_tokens": 123}
    assert _completion_token_limit_kwargs("gpt-4.1-nano", 123) == {"max_tokens": 123}


@pytest.mark.asyncio
async def test_heuristic_uses_live_ontology() -> None:
    with tempfile.TemporaryDirectory() as td:
        onto: OntologyStore = FileOntologyStore(Path(td))
        # Add an ontology skill not in the built-in list
        await onto.upsert_skill("langchain", lang="en")
        await onto.upsert_skill("opencv", lang="en")
        await onto.upsert_seniority("middle")

        from job_ftch.infrastructure.llm.heuristic import HeuristicLLMProvider
        from job_ftch.nodes.extraction import ExtractedJobFields

        provider = HeuristicLLMProvider(ontology_store=onto)
        text = (
            "Computer Vision Engineer\n"
            "Aman\n"
            "1,000,000 to 1,500,000 KZT per month\n"
            "Astana / Remote\n"
            "We need OpenCV and LangChain experience.\n"
        )
        result = await provider.extract(text, ExtractedJobFields)
        skill_names = {s.canonical_name for s in result.skills_explicit}
        assert "opencv" in skill_names
        assert "langchain" in skill_names
        # Location is no longer the company name (lines[1] bug fix).
        # After the three-runs review, location is now correctly extracted
        # from the "City / Work mode" line.
        assert result.location == "Astana"
        assert result.company == "Aman"
        assert result.work_mode == WorkMode.REMOTE
        # Compensation works with currency at the END
        assert result.compensation is not None
        assert result.compensation.currency == "KZT"
        assert result.compensation.min_amount == 1_000_000
        assert result.compensation.max_amount == 1_500_000
        assert result.work_mode == WorkMode.REMOTE


@pytest.mark.asyncio
async def test_heuristic_extracts_company_from_telegram_format() -> None:
    """Telegram DSML format: line 0 = title, line 1 = company, line 2 = salary, line 3 = city/mode.

    Fix #2: HeuristicLLMProvider now extracts company and city from these
    dedicated lines, no longer using ``lines[1]`` as location.
    """
    from job_ftch.domain import Seniority
    from job_ftch.infrastructure.llm.heuristic import HeuristicLLMProvider
    from job_ftch.nodes.extraction import ExtractedJobFields

    provider = HeuristicLLMProvider()
    text = (
        "Senior AI Engineer\n"
        "Acme Corp\n"
        "5,000 - 8,000 USD per month\n"
        "Remote\n"
        "We need Python, PyTorch.\n"
    )
    result = await provider.extract(text, ExtractedJobFields)
    assert result.title == "Senior AI Engineer"
    assert result.company == "Acme Corp"
    # No "City / Mode" pattern in this variant — work_mode still detected from text
    assert result.work_mode == WorkMode.REMOTE
    # No location pattern matched
    assert result.location is None
    # Compensation parsed
    assert result.compensation is not None
    assert result.compensation.currency == "USD"
    assert result.compensation.min_amount == 5000
    assert result.compensation.max_amount == 8000
    # Seniority detected from text
    assert result.seniority == Seniority.SENIOR


@pytest.mark.asyncio
async def test_heuristic_skips_salary_line_when_extracting_company() -> None:
    """If a Telegram message has salary in line 1 (title skipped) or no
    explicit company line, the heuristic must not return the salary as
    the company."""
    from job_ftch.infrastructure.llm.heuristic import HeuristicLLMProvider
    from job_ftch.nodes.extraction import ExtractedJobFields

    provider = HeuristicLLMProvider()
    # No line 1 between title and salary; company is missing.
    text = "Senior Backend Engineer\n1,000,000 KZT per month\nAstana / Remote\n"
    result = await provider.extract(text, ExtractedJobFields)
    assert result.company is None  # no real company line
    assert result.location == "Astana"
    assert result.work_mode == WorkMode.REMOTE
    assert result.compensation is not None


@pytest.mark.asyncio
async def test_post_type_detects_service_offering_pomogu() -> None:
    """Fix #3: 'помогу' / 'оказываю услуги' patterns → ANNOUNCEMENT, not job_posting."""
    from job_ftch.domain import PostType
    from job_ftch.infrastructure.llm.heuristic import HeuristicLLMProvider
    from job_ftch.nodes.extraction import ExtractedJobFields

    provider = HeuristicLLMProvider()
    text = "#помогу #дизайн #ИИ\nПомогаю делать дизайн для вашего бизнеса."
    result = await provider.extract(text, ExtractedJobFields)
    assert result.post_type == PostType.ANNOUNCEMENT


@pytest.mark.asyncio
async def test_post_type_does_not_flag_legitimate_course_mention() -> None:
    """Fix #3: 'course' was removed from announcement tokens — legitimate
    jobs that mention 'online courses' (e.g. mlacademy.ai) should NOT be
    classified as announcements."""
    from job_ftch.domain import PostType
    from job_ftch.infrastructure.llm.heuristic import HeuristicLLMProvider
    from job_ftch.nodes.extraction import ExtractedJobFields

    provider = HeuristicLLMProvider()
    text = (
        "Technical Content Creator\n"
        "mlacademy ai\n"
        "30,000 to 45,000 USD Gross per year\n"
        "Remote\n"
        "ML Academy offers online courses in AI/ML. Create educational content.\n"
    )
    result = await provider.extract(text, ExtractedJobFields)
    assert result.post_type == PostType.JOB_POSTING


@pytest.mark.asyncio
async def test_work_mode_fallback_reads_metadata() -> None:
    """Fix #2: yandex_ai work_mode is in metadata["work_modes"] (list), not in
    text. _fallback_work_mode_from_metadata reads it and returns the
    correct WorkMode."""
    from job_ftch.domain import SourceKind
    from job_ftch.domain.models import RawItem
    from job_ftch.infrastructure.llm.heuristic import HeuristicLLMProvider
    from job_ftch.nodes.extraction import ExtractionNode

    raw = RawItem(
        source_kind=SourceKind.CAREER_SITE,
        source_name="yandex",
        external_id="1",
        text="Alice AI VLM\nМы работаем над созданием агентной системы.",
        url="https://yandex.ru/jobs/1",
        metadata={"work_modes": ["Удалённо"], "cities": []},
    )
    node = ExtractionNode(HeuristicLLMProvider())
    result = await node.process(raw)
    assert result is not None
    assert result.work_mode == WorkMode.REMOTE


@pytest.mark.asyncio
async def test_work_mode_fallback_handles_dict_metadata_entry() -> None:
    """Some career-site APIs (Greenhouse, Lever) return work_mode as a dict
    with ``name``/``label`` keys. Make sure the fallback walks dict entries."""
    from job_ftch.domain import SourceKind
    from job_ftch.domain.models import RawItem
    from job_ftch.infrastructure.llm.heuristic import HeuristicLLMProvider
    from job_ftch.nodes.extraction import ExtractionNode

    raw = RawItem(
        source_kind=SourceKind.CAREER_SITE,
        source_name="greenhouse",
        external_id="2",
        text="Senior Engineer\nWe need someone.",
        url="https://example.com/2",
        metadata={"work_modes": [{"name": "Hybrid"}, {"name": "Remote"}]},
    )
    node = ExtractionNode(HeuristicLLMProvider())
    result = await node.process(raw)
    assert result is not None
    assert result.work_mode == WorkMode.HYBRID


@pytest.mark.asyncio
async def test_work_mode_fallback_returns_unknown_when_metadata_empty() -> None:
    """No work_mode anywhere — heuristic, text, and metadata all return
    unknown. The fallback chain must not raise and must not invent a value."""
    from job_ftch.domain import SourceKind
    from job_ftch.domain.models import RawItem
    from job_ftch.infrastructure.llm.heuristic import HeuristicLLMProvider
    from job_ftch.nodes.extraction import ExtractionNode

    raw = RawItem(
        source_kind=SourceKind.CAREER_SITE,
        source_name="unknown",
        external_id="3",
        text="Engineer\nBody text.",
        url="https://example.com/3",
        metadata={},
    )
    node = ExtractionNode(HeuristicLLMProvider())
    result = await node.process(raw)
    assert result is not None
    assert result.work_mode == WorkMode.UNKNOWN


@pytest.mark.asyncio
async def test_heuristic_compensation_currency_in_front() -> None:
    from job_ftch.infrastructure.llm.heuristic import HeuristicLLMProvider
    from job_ftch.nodes.extraction import ExtractedJobFields

    provider = HeuristicLLMProvider()
    text = "Senior Dev\nAcme\nUSD 5,000 to 8,000 monthly\nRemote\nPython"
    result = await provider.extract(text, ExtractedJobFields)
    assert result.compensation is not None
    assert result.compensation.currency == "USD"
    assert result.compensation.min_amount == 5_000


# ----- LLMRelevanceClassificationNode (point ②) tests -----


def _make_job_record(
    *,
    source_record_id: str | None = "src1",
    title: str = "Senior Engineer",
    relevance_score: float = 0.5,
    description: str = "Test description for the job.",
    work_mode: WorkMode = WorkMode.UNKNOWN,
    language: LanguageCode = LanguageCode.EN,
    company: str | None = None,
) -> JobRecord:
    """Build a minimal valid JobRecord for tests."""
    from job_ftch.domain import SourceKind

    return JobRecord(
        raw_item_id=f"raw-{source_record_id or 'x'}",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="ml_jobs_kz",
        source_record_id=source_record_id,
        title=title,
        description=description,
        work_mode=work_mode,
        language=language,
        company=company,
        relevance_score=relevance_score,
    )


@pytest.mark.asyncio
async def test_relevance_node_skips_high_confidence() -> None:
    from job_ftch.domain import ProfileCatalog, SearchProfile
    from job_ftch.nodes.llm_relevance_classification import LLMRelevanceClassificationNode

    profile = SearchProfile(profile_id="p1", name="p1")
    catalog = ProfileCatalog(profiles=(profile,))
    node = LLMRelevanceClassificationNode(
        llm=_FakeLLM(
            RelevanceClassification(
                decision="accept",
                confidence=0.95,
                reasoning="Should not be called",
            )
        ),
        store=_FakeStore(),
        catalog=catalog,
    )
    item = _make_job_record(relevance_score=0.9)  # above high threshold
    result = await node.process(item)
    assert result is not None
    assert result.routing_decision is None or result.routing_decision != MatchDecision.ACCEPT


@pytest.mark.asyncio
async def test_relevance_node_borderline_calls_llm_and_caches() -> None:
    from job_ftch.domain import ProfileCatalog, SearchProfile
    from job_ftch.nodes.llm_relevance_classification import LLMRelevanceClassificationNode

    profile = SearchProfile(profile_id="p1", name="p1")
    catalog = ProfileCatalog(profiles=(profile,))
    llm = _FakeLLM(
        RelevanceClassification(
            decision="accept",
            confidence=0.85,
            reasoning="Match",
            matched_positive_aspects=("python",),
            mismatched_aspects=(),
        )
    )
    store = _FakeStore()
    node = LLMRelevanceClassificationNode(
        llm=llm,
        store=store,
        catalog=catalog,
        low_threshold=0.2,
        high_threshold=0.5,
        max_per_run=10,
    )
    item = _make_job_record(source_record_id="src1", relevance_score=0.3)
    result = await node.process(item)
    assert result is not None
    assert result.routing_decision is None
    assert result.metadata["evidence_atoms"][-1]["provenance"] == "llm"
    assert llm.calls == 1
    # Second call should hit cache
    item2 = _make_job_record(source_record_id="src1", relevance_score=0.3)
    result2 = await node.process(item2)
    assert result2 is not None
    assert llm.calls == 1  # still 1 (cache hit)


# ----- PresentableTextNode (point ③) tests -----


@pytest.mark.asyncio
async def test_presentable_node_uses_template_when_no_llm() -> None:
    from job_ftch.nodes.presentable_text import PresentableTextNode

    node = PresentableTextNode(
        llm=_FakeLLM(None),  # present() returns None
        store=_FakeStore(),
        max_per_run=0,  # disable LLM calls
    )
    item = _make_job_record(
        title="Senior Engineer",
        company="Acme",
        description="Body",
        work_mode=WorkMode.REMOTE,
        language=LanguageCode.EN,
    )
    result = await node.process(item)
    assert result is not None
    assert result.presentable is not None
    assert result.presentable.title == "Senior Engineer"


@pytest.mark.asyncio
async def test_presentable_node_skips_rejected_item(make_job_record) -> None:
    from job_ftch.domain import MatchDecision
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore
    from job_ftch.nodes.presentable_text import PresentableTextNode

    class _LLM:
        async def present(self, *_args: object) -> object:
            raise AssertionError("rejected item must not call presentation LLM")

    item = make_job_record(routing_decision=MatchDecision.REJECT)
    result = await PresentableTextNode(_LLM(), InMemoryStore()).process(item)

    assert result.presentable is None


@pytest.mark.asyncio
async def test_presentable_node_uses_llm_when_below_max() -> None:
    from job_ftch.nodes.presentable_text import PresentableTextNode

    llm = _FakeLLM(
        PresentableJob(
            title="Clean Title",
            body="Clean body",
            salary_formatted="5K USD",
            location_formatted="Remote",
            contact_section="@user",
            tags=("python", "remote"),
            ats_score=0.95,
            language="en",
        )
    )
    node = PresentableTextNode(llm=llm, store=_FakeStore(), max_per_run=5)
    item = _make_job_record(
        source_record_id="p1",
        title="Original Title",
        description="Original body",
    )
    result = await node.process(item)
    assert result is not None
    assert result.presentable is not None
    assert result.presentable.title == "Clean Title"
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_presentable_node_uses_template_when_shared_budget_exhausted() -> None:
    from job_ftch.nodes.presentable_text import PresentableTextNode

    llm = _FakeLLM(
        PresentableJob(
            title="Clean Title",
            body="Clean body",
            salary_formatted="5K USD",
            location_formatted="Remote",
            contact_section="@user",
            tags=("python",),
            ats_score=0.95,
            language="en",
        )
    )
    node = PresentableTextNode(
        llm=llm,
        store=_FakeStore(),
        max_per_run=5,
        budget=AsyncCallBudget(1),
    )
    item1 = _make_job_record(
        source_record_id="p1", title="Original Title", description="Original body"
    )
    item2 = _make_job_record(
        source_record_id="p2", title="Another Title", description="Another body"
    )

    first = await node.process(item1)
    second = await node.process(item2)

    assert first.presentable is not None
    assert first.presentable.title == "Clean Title"
    assert second.presentable is not None
    assert second.presentable.title == "Another Title"
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_relevance_node_falls_back_when_shared_budget_exhausted() -> None:
    from job_ftch.domain import ProfileCatalog, SearchProfile
    from job_ftch.nodes.llm_relevance_classification import LLMRelevanceClassificationNode

    profile = SearchProfile(profile_id="p1", name="p1")
    catalog = ProfileCatalog(profiles=(profile,))
    llm = _FakeLLM(
        RelevanceClassification(
            decision="accept",
            confidence=0.85,
            reasoning="Match",
            matched_positive_aspects=("python",),
            mismatched_aspects=(),
        )
    )
    node = LLMRelevanceClassificationNode(
        llm=llm,
        store=_FakeStore(),
        catalog=catalog,
        low_threshold=0.2,
        high_threshold=0.5,
        max_per_run=10,
        budget=AsyncCallBudget(1),
    )
    first = await node.process(_make_job_record(source_record_id="src1", relevance_score=0.3))
    second = await node.process(_make_job_record(source_record_id="src2", relevance_score=0.3))

    assert first is not None
    assert first.routing_decision is None
    assert first.metadata["evidence_atoms"][-1]["provenance"] == "llm"
    assert second is not None
    assert llm.calls == 1


def test_presentable_prompt_restricts_output_fields() -> None:
    from job_ftch.nodes.presentable_text import _build_presentable_prompt

    item = _make_job_record(
        title="Original Title",
        description="Original body",
    ).model_copy(
        update={
            "metadata": {
                "ontology_snapshots": {"default": {"payload_json": "private ontology"}},
                "bgem3_dense": [0.1, 0.2],
                "bgem3_sparse": {"private-token": 1.0},
            },
        }
    )

    prompt = _build_presentable_prompt(item)

    assert "Return only these PresentableJob fields" in prompt
    assert "Original Title" in prompt
    assert "Original body" in prompt
    assert "private ontology" not in prompt
    assert "bgem3_dense" not in prompt
    assert "private-token" not in prompt


@pytest.mark.asyncio
async def test_decision_extraction_combines_llm_work_and_caches_result() -> None:
    from job_ftch.domain import ProfileCatalog, RawItem, SearchProfile, SourceKind
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore
    from job_ftch.nodes.decision_extraction import DecisionExtractionNode

    class _CombinedLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def extract(self, _prompt: str, schema: object) -> object:
            self.calls += 1
            return schema.model_validate(
                {  # type: ignore[union-attr]
                    "title": "AI Product Engineer",
                    "company": "Acme",
                    "description": "Build LLM-powered product features for customers.",
                    "decision": "accept",
                    "confidence": 0.91,
                    "reasoning": "Core work is LLM product engineering.",
                }
            )

    profile = SearchProfile(
        profile_id="p1",
        name="AI roles",
        target_roles=("AI Product Engineer",),
    )
    llm = _CombinedLLM()
    node = DecisionExtractionNode(
        llm,  # type: ignore[arg-type]
        InMemoryStore(),
        ProfileCatalog(profiles=(profile,)),
        target_roles=("AI Product Engineer",),
        scope="core",
    )
    item = RawItem(
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="jobs",
        external_id="one-call",
        text="AI Product Engineer at Acme. Build LLM-powered product features for customers.",
    )

    first = await node.process(item)
    second = await node.process(item)

    assert first is not None
    assert first.metadata["_llm_relevance"]["decision"] == "accept"
    assert first.metadata["_llm_relevance"]["combined_with_extraction"] is True
    assert second is not None
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_decision_extraction_times_out_without_blocking_pipeline() -> None:
    import asyncio

    from job_ftch.domain import ProfileCatalog, RawItem, SearchProfile, SourceKind
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore
    from job_ftch.nodes.decision_extraction import DecisionExtractionNode

    class _SlowLLM:
        async def extract(self, _prompt: str, _schema: object) -> object:
            await asyncio.sleep(1)
            raise AssertionError("unreachable")

    node = DecisionExtractionNode(
        _SlowLLM(),  # type: ignore[arg-type]
        InMemoryStore(),
        ProfileCatalog(profiles=(SearchProfile(profile_id="p1", name="AI roles"),)),
        request_timeout_seconds=0.001,
        scope="core",
    )
    result = await node.process(
        RawItem(
            source_kind=SourceKind.TELEGRAM_CHANNEL,
            source_name="jobs",
            external_id="slow-one-call",
            text="AI Product Engineer",
        )
    )

    assert result is not None
    assert "_llm_relevance" not in result.metadata


@pytest.mark.asyncio
async def test_accept_template_presentation_only_formats_accepts(make_job_record) -> None:
    from job_ftch.domain import MatchDecision
    from job_ftch.nodes.accept_template_presentation import AcceptTemplatePresentationNode

    node = AcceptTemplatePresentationNode()
    accepted = await node.process(make_job_record(routing_decision=MatchDecision.ACCEPT))
    rejected = await node.process(make_job_record(routing_decision=MatchDecision.REJECT))

    assert accepted.presentable is not None
    assert rejected.presentable is None


@pytest.mark.asyncio
async def test_routing_applies_combined_llm_confidence(make_job_record) -> None:
    from job_ftch.domain import MatchDecision
    from job_ftch.nodes.routing import RoutingNode

    item = make_job_record(
        relevance_score=0.31,
        metadata={
            "_llm_relevance": {
                "decision": "accept",
                "confidence": 0.91,
                "reasoning": "match",
                "combined_with_extraction": True,
            }
        },
    )

    result = await RoutingNode().process(item)

    assert result.routing_decision is MatchDecision.ACCEPT
    assert result.relevance_score == 0.91
