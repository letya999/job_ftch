from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from job_ftch.application.ontology_compiler import (
    LabeledOntologyShot,
    LLMOntologyCandidateChunk,
    OntologyCandidateChunk,
    _restore_projection_from_candidates,
    compile_ontology_from_shots,
    materialize_compiled_ontology,
    sanitize_compiled_ontology,
    shot_id_for_text,
)
from job_ftch.application.ontology_enrichment import (
    _heuristic_materialized_from_shot,
    add_example_to_profile_with_enrichment,
    compile_profile_ontology,
)
from job_ftch.application.resume_extraction import add_example_to_profile
from job_ftch.domain import (
    CandidateIdentity,
    CandidateProfile,
    CompiledOntology,
    CompiledOntologyRelation,
    CompiledOntologyTerm,
    ManagedCandidateProfile,
    SearchProfile,
)
from job_ftch.infrastructure.ontology.file_store import FileOntologyStore

_PROMPT = Path("config/prompts/ontology_compiler_v2.yaml")
_POS = "Senior LLM Engineer / Python, PyTorch, RAG, Docker, FastAPI, Git"
_NEG = "Sales manager / recruiter / accountant only, no engineering"
_NEG_REFUSAL = "Python developer. RAG и LLM не использовал"
_REQUIRE_OBJECTS = (
    "ai assistants",
    "docker",
    "document search",
    "fastapi",
    "git",
    "knowledge management",
    "next.js",
    "node.js",
    "playwright",
    "postgresql",
    "python",
    "redis",
    "rest api",
    "supabase",
    "typescript",
    "vercel",
    "function calling",
    "gitlab ci",
    "langfuse",
    "langgraph",
    "openai api",
    "pgvector",
    "qdrant",
    "rag",
    "sql",
    "structured outputs",
    "telegram bot api",
    "ml researcher",
    "cuda",
    "pytorch",
    "transformers",
    "langchain",
    "qwen",
    "vllm",
)


def _term(
    *,
    canonical: str,
    entity_type: str,
    semantic_role: str,
    polarity: str,
    shot_id: str,
    source_section: str = "requirements",
) -> CompiledOntologyTerm:
    return CompiledOntologyTerm(
        canonical=canonical,
        entity_type=entity_type,  # type: ignore[arg-type]
        semantic_role=semantic_role,  # type: ignore[arg-type]
        polarity=polarity,  # type: ignore[arg-type]
        scope="anti" if polarity == "negative" else "target",  # type: ignore[arg-type]
        source_section=source_section,  # type: ignore[arg-type]
        evidence_shot_ids=(shot_id,),
        support_count=1,
        confidence=0.9,
        weight=0.85,
        accepted=False,
    )


class _ScriptedLLM:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.prompts: list[str] = []
        self.timeouts: list[float | None] = []

    async def classify(
        self,
        prompt: str,
        schema: type[Any],
        *,
        timeout_seconds: float | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        del schema, max_tokens
        self.calls += 1
        self.prompts.append(prompt)
        self.timeouts.append(timeout_seconds)
        if not self._responses:
            raise RuntimeError("InstructorRetryException")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _empty_profile() -> ManagedCandidateProfile:
    return ManagedCandidateProfile(
        user_id="u",
        profile_id="p",
        profile=CandidateProfile(
            identity=CandidateIdentity(candidate_id="u", display_name="u"),
            search_profiles=(SearchProfile(),),
        ),
    )


@pytest.mark.asyncio
async def test_compile_skips_failed_chunk_and_keeps_later_terms() -> None:
    pos_id = shot_id_for_text(_POS)
    neg_id = shot_id_for_text(_NEG)
    good = OntologyCandidateChunk(
        terms=(
            _term(
                canonical="python",
                entity_type="skill",
                semantic_role="target_skill",
                polarity="positive",
                shot_id=pos_id,
            ),
            _term(
                canonical="sales manager",
                entity_type="role",
                semantic_role="anti_role",
                polarity="negative",
                shot_id=neg_id,
                source_section="title",
            ),
        )
    )
    llm = _ScriptedLLM(
        [
            RuntimeError("InstructorRetryException"),
            good,
            CompiledOntology(summary="ok"),
        ]
    )
    # chunk_size=8: first parallel chunk fails, later chunk still contributes terms.
    shots = [("positive_job", f"Backend engineer Java Spring {idx}") for idx in range(8)] + [
        ("positive_job", _POS),
        ("negative_job", _NEG),
    ]
    result = await compile_ontology_from_shots(shots=shots, llm=llm, prompt_path=_PROMPT)
    assert "python" in result.materialized.positive_skills
    assert result.materialized.negative_roles or result.materialized.anti_patterns


@pytest.mark.asyncio
async def test_compile_reattaches_unknown_evidence_ids() -> None:
    pos_id = shot_id_for_text(_POS)
    chunk = OntologyCandidateChunk(
        terms=(
            _term(
                canonical="python",
                entity_type="skill",
                semantic_role="target_skill",
                polarity="positive",
                shot_id="not-a-real-id",
            ),
        )
    )
    llm = _ScriptedLLM([chunk, CompiledOntology(summary="ok")])
    result = await compile_ontology_from_shots(
        shots=[("positive_job", _POS)],
        llm=llm,
        prompt_path=_PROMPT,
    )
    restored = [term for term in result.ontology.terms if term.canonical == "python"]
    assert restored
    assert pos_id in restored[0].evidence_shot_ids
    assert "python" in result.materialized.positive_skills


@pytest.mark.asyncio
async def test_compile_does_not_raise_when_negatives_do_not_project() -> None:
    pos_id = shot_id_for_text(_POS)
    chunk = OntologyCandidateChunk(
        terms=(
            _term(
                canonical="python",
                entity_type="skill",
                semantic_role="target_skill",
                polarity="positive",
                shot_id=pos_id,
            ),
        )
    )
    llm = _ScriptedLLM([chunk, CompiledOntology(summary="ok")])
    result = await compile_ontology_from_shots(
        shots=[("positive_job", _POS), ("negative_job", _NEG)],
        llm=llm,
        prompt_path=_PROMPT,
    )
    assert "python" in result.materialized.positive_skills
    assert "missing_negative_projection" in result.ontology.warnings
    assert not any("coverage" in prompt.casefold() for prompt in llm.prompts)


@pytest.mark.asyncio
async def test_compile_profile_ontology_falls_back_when_llm_raises(tmp_path: Path) -> None:
    class _Boom:
        async def classify(self, prompt: str, schema: type[Any]) -> Any:
            del prompt, schema
            raise RuntimeError("InstructorRetryException")

    store = FileOntologyStore(tmp_path)
    managed = _empty_profile()
    managed = add_example_to_profile(managed, _POS, kind="positive_job")
    managed = add_example_to_profile(
        managed,
        "Java Spring enterprise accountant recruiter, no Python",
        kind="negative_job",
    )
    payload = await compile_profile_ontology(managed, llm=_Boom(), ontology_store=store)
    assert int(payload["pos_added"]) > 0
    skills = await store.list_skills()
    anti = await store.list_anti_patterns()
    neg = await store.list_negative_skills()
    keywords = await store.list_negative_keywords()
    blob = " ".join(skills).lower()
    assert "python" in blob or "llm" in blob or "pytorch" in blob or "rag" in blob
    assert anti or neg or keywords


def _requires(subject: str, obj: str, shot_id: str) -> CompiledOntologyRelation:
    return CompiledOntologyRelation(
        subject=subject,
        predicate="requires",
        object=obj,
        polarity="positive",
        evidence_shot_ids=(shot_id,),
        confidence=0.5,
        weight=0.5,
    )


def _accepted(**kwargs: Any) -> CompiledOntologyTerm:
    return _term(**kwargs).model_copy(update={"accepted": True})


def test_requires_relations_materialize_as_positive_skills() -> None:
    assert len(_REQUIRE_OBJECTS) == 34
    pos_id = "pos-1"
    neg_id = "neg-1"
    shots = (
        LabeledOntologyShot(shot_id=pos_id, kind="positive_job", text=_POS),
        LabeledOntologyShot(shot_id=neg_id, kind="negative_resume", text=_NEG_REFUSAL),
    )
    ontology = CompiledOntology(
        summary="gpt compile-pass",
        terms=(
            _accepted(
                canonical="rag",
                entity_type="skill",
                semantic_role="anti_skill",
                polarity="negative",
                shot_id=neg_id,
            ),
            _accepted(
                canonical="insufficient rag",
                entity_type="anti_pattern",
                semantic_role="anti_pattern",
                polarity="negative",
                shot_id=neg_id,
            ),
            _accepted(
                canonical="llm",
                entity_type="skill",
                semantic_role="anti_skill",
                polarity="negative",
                shot_id=neg_id,
            ),
        ),
        relations=tuple(
            _requires("ai developer" if index < 16 else "llm", obj, pos_id)
            for index, obj in enumerate(_REQUIRE_OBJECTS)
        ),
    )

    restored = sanitize_compiled_ontology(_restore_projection_from_candidates(ontology, (), shots))
    materialized, _stats = materialize_compiled_ontology(restored)

    assert "python" in materialized.positive_skills
    assert "docker" in materialized.positive_skills
    assert "fastapi" in materialized.positive_skills
    assert "git" in materialized.positive_skills
    assert "rag" in materialized.positive_skills
    assert "python" not in materialized.negative_skills
    assert "python" not in materialized.anti_patterns
    assert "python" not in {term for term, _weight in materialized.negative_keywords}
    assert "insufficient rag" in materialized.anti_patterns
    assert "rag" not in materialized.negative_skills
    assert "llm" not in materialized.negative_skills
    assert "insufficient llm" in materialized.anti_patterns


@pytest.mark.asyncio
async def test_compile_keeps_compile_pass_when_restore_thresholds_fail() -> None:
    pos_id = shot_id_for_text(_POS)
    neg_id = shot_id_for_text(_NEG)
    weak = OntologyCandidateChunk(
        terms=(
            CompiledOntologyTerm(
                canonical="python",
                entity_type="skill",
                semantic_role="target_skill",
                polarity="positive",
                scope="target",
                source_section="unknown",
                evidence_shot_ids=(pos_id,),
                support_count=1,
                confidence=0.4,
                weight=0.4,
                accepted=False,
            ),
            _term(
                canonical="sales manager",
                entity_type="role",
                semantic_role="anti_role",
                polarity="negative",
                shot_id=neg_id,
                source_section="title",
            ),
        )
    )
    compile_pass = CompiledOntology(
        summary="compile-pass",
        terms=(
            CompiledOntologyTerm(
                canonical="python",
                entity_type="skill",
                semantic_role="target_skill",
                polarity="positive",
                scope="target",
                source_section="unknown",
                evidence_shot_ids=(pos_id,),
                support_count=1,
                confidence=0.5,
                weight=0.5,
                accepted=True,
            ),
            _accepted(
                canonical="sales manager",
                entity_type="role",
                semantic_role="anti_role",
                polarity="negative",
                shot_id=neg_id,
                source_section="title",
            ),
        ),
    )
    llm = _ScriptedLLM([weak, compile_pass])
    result = await compile_ontology_from_shots(
        shots=[("positive_job", _POS), ("negative_job", _NEG)],
        llm=llm,
        prompt_path=_PROMPT,
        compile_timeout_seconds=120.0,
    )
    assert "python" in result.materialized.positive_skills
    assert llm.timeouts[0] == 120.0
    assert llm.timeouts[1] == 120.0


@pytest.mark.asyncio
async def test_compile_profile_ontology_keeps_gpt_relations_not_heuristic(
    tmp_path: Path,
) -> None:
    pos_text = "Senior LLM Engineer / Python, Docker, FastAPI. Previously product manager."
    pos_id = shot_id_for_text(pos_text)
    neg_id = shot_id_for_text(_NEG_REFUSAL)
    relations = tuple(_requires("ai developer", obj, pos_id) for obj in _REQUIRE_OBJECTS)
    weak = OntologyCandidateChunk(
        terms=(
            CompiledOntologyTerm(
                canonical="python",
                entity_type="skill",
                semantic_role="target_skill",
                polarity="positive",
                scope="target",
                source_section="unknown",
                evidence_shot_ids=(pos_id,),
                support_count=1,
                confidence=0.4,
                weight=0.4,
                accepted=False,
            ),
            _term(
                canonical="sales manager",
                entity_type="role",
                semantic_role="anti_role",
                polarity="negative",
                shot_id=neg_id,
                source_section="title",
            ),
        )
    )
    compile_pass = CompiledOntology(
        summary="gpt",
        terms=(
            _accepted(
                canonical="rag",
                entity_type="skill",
                semantic_role="anti_skill",
                polarity="negative",
                shot_id=neg_id,
            ),
            _accepted(
                canonical="insufficient rag",
                entity_type="anti_pattern",
                semantic_role="anti_pattern",
                polarity="negative",
                shot_id=neg_id,
            ),
        ),
        relations=relations,
    )
    llm = _ScriptedLLM([weak, compile_pass])
    store = FileOntologyStore(tmp_path)
    managed = _empty_profile()
    managed = add_example_to_profile(managed, pos_text, kind="positive_job")
    managed = add_example_to_profile(managed, _NEG_REFUSAL, kind="negative_resume")
    payload = await compile_profile_ontology(managed, llm=llm, ontology_store=store)
    assert payload.get("model") != "heuristic"
    skills = await store.list_skills()
    roles = await store.list_roles()
    anti = await store.list_anti_patterns()
    neg_skills = await store.list_negative_skills()
    assert "python" in skills
    assert "docker" in skills
    assert "fastapi" in skills
    assert "product manager" not in roles
    assert "python" not in anti
    assert "python" not in neg_skills
    assert int(payload["pos_added"]) > 0
    graphs = json.loads((tmp_path / "ontology_shot_graphs.json").read_text(encoding="utf-8"))
    graph = next(iter(graphs.values()))
    assert graph.get("nodes")
    assert graph.get("edges")


def test_heuristic_anti_uses_explicit_refusal_not_mentioned_skills() -> None:
    materialized = _heuristic_materialized_from_shot(_NEG_REFUSAL, "negative_resume")
    assert "python" not in materialized.negative_skills
    assert "python" not in materialized.anti_patterns
    blob = " ".join((*materialized.anti_patterns, *materialized.negative_skills)).casefold()
    assert "llm" in blob or "rag" in blob


def test_llm_dto_coerces_skill_role_and_ignores_extra() -> None:
    chunk = LLMOntologyCandidateChunk.model_validate(
        {
            "terms": {
                "canonical": "python",
                "entity_type": "skill",
                "semantic_role": "skill",
                "polarity": "positive",
                "scope": "target",
                "source_section": "experience",
                "aliases": "py",
                "evidence_shot_ids": "abc",
                "support_count": "2",
                "confidence": 1.4,
                "junk": "ignored",
            },
            "surprise": True,
        }
    )
    domain = chunk.to_domain()
    assert domain.terms[0].semantic_role == "target_skill"
    assert domain.terms[0].source_section == "skills"
    assert domain.terms[0].confidence == 1.0
    assert domain.terms[0].aliases == ("py",)
    assert domain.terms[0].evidence_shot_ids == ("abc",)
    assert domain.terms[0].support_count == 2


def test_llm_dto_accepts_json_lists() -> None:
    chunk = LLMOntologyCandidateChunk.model_validate(
        {
            "terms": [
                {
                    "canonical": "llm engineer",
                    "entity_type": "role",
                    "semantic_role": "target_role",
                    "polarity": "positive",
                    "confidence": 0.7,
                    "accepted": False,
                }
            ],
            "relations": [
                {
                    "subject": "llm engineer",
                    "predicate": "requires",
                    "object": "python",
                    "evidence_shot_ids": ["shot-1"],
                }
            ],
        }
    )
    domain = chunk.to_domain()
    assert domain.terms[0].canonical == "llm engineer"
    assert domain.relations[0].object == "python"


@pytest.mark.asyncio
async def test_compile_keeps_later_chunks_after_two_consecutive_failures() -> None:
    pos_id = shot_id_for_text(_POS)
    good = OntologyCandidateChunk(
        terms=(
            _term(
                canonical="python",
                entity_type="skill",
                semantic_role="target_skill",
                polarity="positive",
                shot_id=pos_id,
            ),
        )
    )
    llm = _ScriptedLLM(
        [
            RuntimeError("fail-1"),
            RuntimeError("fail-2"),
            good,
            CompiledOntology(summary="ok"),
            CompiledOntology(summary="ok"),
        ]
    )
    shots = [
        ("positive_job", "Backend engineer Java Spring"),
        ("positive_job", "Another Java vacancy"),
        ("positive_job", "Enterprise Java lead"),
        ("positive_job", "Spring Boot developer"),
        ("positive_job", "Platform engineer Kafka"),
        ("positive_job", "Data platform Spark"),
        ("positive_job", "Cloud engineer Terraform"),
        ("positive_job", "SRE on-call Prometheus"),
        ("positive_job", _POS),
        ("positive_job", "LLM engineer RAG FastAPI"),
        ("positive_job", "Agent engineer LangGraph"),
        ("positive_job", "Eval engineer Langfuse"),
    ]
    result = await compile_ontology_from_shots(shots=shots, llm=llm, prompt_path=_PROMPT)
    assert "python" in result.materialized.positive_skills
    assert llm.calls >= 4


@pytest.mark.asyncio
async def test_compile_runs_coverage_after_candidate_chunks_fail() -> None:
    pos_id = shot_id_for_text(_POS)
    coverage = OntologyCandidateChunk(
        terms=(
            _term(
                canonical="python",
                entity_type="skill",
                semantic_role="target_skill",
                polarity="positive",
                shot_id=pos_id,
            ),
        )
    )
    llm = _ScriptedLLM(
        [
            RuntimeError("fail-1"),
            RuntimeError("fail-2"),
            coverage,
            coverage,
            coverage,
            CompiledOntology(summary="ok"),
            CompiledOntology(summary="ok"),
        ]
    )
    shots = [
        ("positive_job", "Backend engineer Java Spring"),
        ("positive_job", "Another Java vacancy"),
        ("positive_job", "Enterprise Java lead"),
        ("positive_job", "Spring Boot developer"),
        ("positive_job", _POS),
        ("positive_job", "LLM engineer RAG FastAPI"),
    ]
    result = await compile_ontology_from_shots(shots=shots, llm=llm, prompt_path=_PROMPT)
    assert "python" in result.materialized.positive_skills
    assert any(
        "coverage" in prompt.casefold() or "named-technology" in prompt.casefold()
        for prompt in llm.prompts
    )


@pytest.mark.asyncio
async def test_compile_profile_ontology_uses_compiler_model_not_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from job_ftch.config import Settings

    pos_id = shot_id_for_text(_POS)
    neg_id = shot_id_for_text(_NEG)
    chunk = OntologyCandidateChunk(
        terms=(
            _term(
                canonical="python",
                entity_type="skill",
                semantic_role="target_skill",
                polarity="positive",
                shot_id=pos_id,
            ),
            _term(
                canonical="llm engineer",
                entity_type="role",
                semantic_role="target_role",
                polarity="positive",
                shot_id=pos_id,
                source_section="title",
            ),
            _term(
                canonical="insufficient llm",
                entity_type="anti_pattern",
                semantic_role="anti_pattern",
                polarity="negative",
                shot_id=neg_id,
                source_section="anti_reason",
            ),
        )
    )
    compiled = CompiledOntology(
        summary="compiler",
        terms=(
            _accepted(
                canonical="python",
                entity_type="skill",
                semantic_role="target_skill",
                polarity="positive",
                shot_id=pos_id,
            ),
            _accepted(
                canonical="llm engineer",
                entity_type="role",
                semantic_role="target_role",
                polarity="positive",
                shot_id=pos_id,
                source_section="title",
            ),
            _accepted(
                canonical="insufficient llm",
                entity_type="anti_pattern",
                semantic_role="anti_pattern",
                polarity="negative",
                shot_id=neg_id,
                source_section="anti_reason",
            ),
        ),
    )

    class _Extraction:
        model_id = "gpt-5.4-nano"

        async def classify(self, *args: object, **kwargs: object) -> Any:
            raise AssertionError("extraction llm must not compile ontology")

    class _Compiler:
        model_id = "gpt-4.1-mini"

        async def classify(self, prompt: str, schema: type[Any], **kwargs: object) -> Any:
            del prompt, kwargs
            name = getattr(schema, "__name__", "")
            if "Candidate" in name:
                return chunk
            return compiled

    captured: dict[str, object] = {}

    def fake_settings() -> Settings:
        return Settings(
            llm_backend="openai",
            openai_model="gpt-5.4-nano",
            ontology_compiler_model="gpt-4.1-mini",
            ontology_compiler_timeout_seconds=120.0,
            openai_api_key="fixture-api-key",  # pragma: allowlist secret
            tracing_enabled=False,
            openobserve_enabled=False,
            embedding_enabled=False,
            bgem3_enabled=False,
        )

    def fake_create_llm(settings: Settings) -> object:
        captured["openai_model"] = settings.openai_model
        captured["timeout"] = settings.openai_timeout_seconds
        return _Compiler()

    monkeypatch.setattr("job_ftch.config.get_settings", fake_settings)
    monkeypatch.setattr("job_ftch.application.registry.create_llm", fake_create_llm)

    store = FileOntologyStore(tmp_path)
    managed = _empty_profile()
    managed = add_example_to_profile(managed, _POS, kind="positive_job")
    managed = add_example_to_profile(managed, _NEG, kind="negative_job")
    payload = await compile_profile_ontology(managed, llm=_Extraction(), ontology_store=store)
    assert captured["openai_model"] == "gpt-4.1-mini"
    assert captured["timeout"] == 120.0
    assert payload.get("model") == "gpt-4.1-mini"
    roles = await store.list_roles()
    skills = await store.list_skills()
    anti = await store.list_anti_patterns()
    assert any("llm engineer" in role for role in roles)
    assert "python" in skills
    assert anti


@pytest.mark.asyncio
async def test_per_shot_enrichment_uses_compiler_model_not_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from job_ftch.config import Settings

    pos_id = shot_id_for_text(_POS)
    compiled = CompiledOntology(
        summary="compiler",
        terms=(
            _accepted(
                canonical="python",
                entity_type="skill",
                semantic_role="target_skill",
                polarity="positive",
                shot_id=pos_id,
            ),
            _accepted(
                canonical="llm engineer",
                entity_type="role",
                semantic_role="target_role",
                polarity="positive",
                shot_id=pos_id,
                source_section="title",
            ),
        ),
    )
    chunk = OntologyCandidateChunk(terms=compiled.terms)

    class _Extraction:
        model_id = "gpt-5.4-nano"

        async def classify(self, *args: object, **kwargs: object) -> Any:
            raise AssertionError("extraction llm must not compile ontology")

    class _Compiler:
        model_id = "gpt-4.1-mini"

        async def classify(self, prompt: str, schema: type[Any], **kwargs: object) -> Any:
            del prompt, kwargs
            name = getattr(schema, "__name__", "")
            if "Candidate" in name:
                return chunk
            return compiled

    captured: dict[str, object] = {}

    def fake_settings() -> Settings:
        return Settings(
            llm_backend="openai",
            openai_model="gpt-5.4-nano",
            ontology_compiler_model="gpt-4.1-mini",
            ontology_compiler_timeout_seconds=120.0,
            openai_api_key="fixture-api-key",  # pragma: allowlist secret
            tracing_enabled=False,
            openobserve_enabled=False,
            embedding_enabled=False,
            bgem3_enabled=False,
        )

    def fake_create_llm(settings: Settings) -> object:
        captured["openai_model"] = settings.openai_model
        captured["timeout"] = settings.openai_timeout_seconds
        return _Compiler()

    monkeypatch.setattr("job_ftch.config.get_settings", fake_settings)
    monkeypatch.setattr("job_ftch.application.registry.create_llm", fake_create_llm)

    store = FileOntologyStore(tmp_path)
    await add_example_to_profile_with_enrichment(
        _empty_profile(),
        _POS,
        kind="positive_job",
        llm=_Extraction(),
        ontology_store=store,
    )
    assert captured["openai_model"] == "gpt-4.1-mini"
    assert captured["timeout"] == 120.0
    roles = await store.list_roles()
    skills = await store.list_skills()
    assert any("llm engineer" in role for role in roles)
    assert "python" in skills


@pytest.mark.asyncio
async def test_compile_profile_ontology_replaces_stale_live_roles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from job_ftch.config import Settings

    pos_id = shot_id_for_text(_POS)
    compiled = CompiledOntology(
        summary="compiler",
        terms=(
            _accepted(
                canonical="llm engineer",
                entity_type="role",
                semantic_role="target_role",
                polarity="positive",
                shot_id=pos_id,
                source_section="title",
            ),
        ),
    )
    chunk = OntologyCandidateChunk(terms=compiled.terms)

    class _Compiler:
        model_id = "gpt-4.1-mini"

        async def classify(self, prompt: str, schema: type[Any], **kwargs: object) -> Any:
            del prompt, kwargs
            name = getattr(schema, "__name__", "")
            if "Candidate" in name:
                return chunk
            return compiled

    def fake_settings() -> Settings:
        return Settings(
            llm_backend="openai",
            openai_model="gpt-5.4-nano",
            ontology_compiler_model="gpt-4.1-mini",
            ontology_compiler_timeout_seconds=120.0,
            openai_api_key="fixture-api-key",  # pragma: allowlist secret
            tracing_enabled=False,
            openobserve_enabled=False,
            embedding_enabled=False,
            bgem3_enabled=False,
        )

    monkeypatch.setattr("job_ftch.config.get_settings", fake_settings)
    monkeypatch.setattr("job_ftch.application.registry.create_llm", lambda settings: _Compiler())

    store = FileOntologyStore(tmp_path)
    await store.upsert_role("data scientist", polarity="positive")
    managed = add_example_to_profile(_empty_profile(), _POS, kind="positive_job")
    managed = add_example_to_profile(managed, _NEG, kind="negative_job")
    await compile_profile_ontology(managed, llm=_Compiler(), ontology_store=store)
    roles = await store.list_roles()
    assert any("llm engineer" in role for role in roles)
    assert "data scientist" not in roles
