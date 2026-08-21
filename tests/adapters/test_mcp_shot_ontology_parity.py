"""MCP shot ingest must populate ontology tables like the Telegram bot path."""

from __future__ import annotations

from typing import Any

import pytest

from job_ftch.adapters.mcp.product_surface import add_shots
from job_ftch.application.ontology_compiler import (
    OntologyCandidateChunk,
    shot_id_for_text,
)
from job_ftch.application.registry import create_ontology_store, create_store
from job_ftch.application.tenant_store import TenantStore
from job_ftch.config import Settings
from job_ftch.domain import (
    CandidateIdentity,
    CandidateProfile,
    CompiledOntology,
    CompiledOntologyTerm,
    ManagedCandidateProfile,
    SearchProfile,
)
from job_ftch.infrastructure.llm.heuristic import HeuristicLLMProvider


class _FakeRuntime:
    def __init__(self, store: TenantStore, ontology_store: object, llm: object) -> None:
        self.store = store
        self.ontology_store = ontology_store
        self.llm_provider = llm
        self.embedding_provider = None


class _FakeRunner:
    def __init__(self, runtime: _FakeRuntime) -> None:
        self._runtime = runtime
        self._profiles: dict[tuple[str, str, str], ManagedCandidateProfile] = {}

    def get_runtime(self, tenant_id: str) -> _FakeRuntime:
        del tenant_id
        return self._runtime

    async def list_candidate_profiles(self, tenant_id: str, user_id: str) -> list[dict[str, Any]]:
        del tenant_id
        out: list[dict[str, Any]] = []
        for (tid, uid, pid), managed in self._profiles.items():
            del tid
            if uid != user_id:
                continue
            out.append(
                {
                    "profile_id": pid,
                    "user_id": uid,
                    "active": True,
                    "name": managed.profile_id,
                }
            )
        return out

    async def get_candidate_profile(
        self, tenant_id: str, user_id: str, profile_id: str
    ) -> ManagedCandidateProfile | None:
        return self._profiles.get((tenant_id, user_id, profile_id))

    async def save_and_activate_candidate_profile(
        self, tenant_id: str, managed: ManagedCandidateProfile
    ) -> dict[str, Any]:
        self._profiles[(tenant_id, managed.user_id, managed.profile_id)] = managed
        await self._runtime.store.save_and_activate_candidate_profile(managed)
        return {"profile_id": managed.profile_id, "active": True}


@pytest.mark.asyncio
async def test_mcp_add_shots_fills_ontology_with_heuristic(tmp_path: Any) -> None:
    settings = Settings(
        llm_backend="heuristic",
        store_backend="sqlite",
        store_path=tmp_path / "store.db",
        tracing_enabled=False,
        openobserve_enabled=False,
        job_backend="sqlite",
        search_backend="sqlite",
        job_group_store_backend="sqlite",
        bgem3_enabled=False,
        embedding_enabled=False,
    )
    base_store = create_store(settings)
    tenant_store = TenantStore("local_mcp", base_store)
    ontology = create_ontology_store(settings)
    runtime = _FakeRuntime(tenant_store, ontology, HeuristicLLMProvider())
    runner = _FakeRunner(runtime)

    # Seed an empty profile so ensure_managed_profile finds one.
    empty = ManagedCandidateProfile(
        user_id="mcp",
        profile_id="mcp_default",
        profile=CandidateProfile(
            identity=CandidateIdentity(candidate_id="mcp", display_name="mcp"),
            search_profiles=(SearchProfile(),),
        ),
    )
    await runner.save_and_activate_candidate_profile("local_mcp", empty)

    result = await add_shots(
        runner,
        tenant_id="local_mcp",
        user_id="mcp",
        polarity="positive",
        kind="job",
        text=(
            "LLM Engineer / Senior Python\n"
            "Company: Alem AI\n"
            "Requirements: Python, PyTorch, Docker, Kubernetes, RAG, FastAPI, SQL\n"
        ),
        profile_id="mcp_default",
    )
    assert result["added"] == 1
    assert result["ontology_store"] is True
    assert result["ontology_errors"] == []

    skills = await ontology.list_skills()
    assert "python" in skills
    assert "pytorch" in skills or "rag" in skills or "docker" in skills
    seniority = await ontology.list_seniority()
    assert "senior" in seniority
    roles = await ontology.list_roles()
    assert any("llm engineer" in r or "engineer" in r for r in roles) or skills

    neg = await add_shots(
        runner,
        tenant_id="local_mcp",
        user_id="mcp",
        polarity="negative",
        kind="job",
        text="Data Scientist with pandas, scikit-learn, SQL and A/B testing only, no LLM",
        profile_id="mcp_default",
    )
    assert neg["added"] == 1
    anti = await ontology.list_anti_patterns()
    neg_skills = await ontology.list_negative_skills()
    assert anti or neg_skills or await ontology.list_negative_keywords()


@pytest.mark.asyncio
async def test_mcp_add_shots_compiles_recipe_scale_profile(tmp_path: Any) -> None:
    settings = Settings(
        llm_backend="heuristic",
        store_backend="sqlite",
        store_path=tmp_path / "store.db",
        tracing_enabled=False,
        openobserve_enabled=False,
        job_backend="sqlite",
        search_backend="sqlite",
        job_group_store_backend="sqlite",
        bgem3_enabled=False,
        embedding_enabled=False,
    )
    tenant_store = TenantStore("local_mcp", create_store(settings))
    ontology = create_ontology_store(settings)
    runner = _FakeRunner(_FakeRuntime(tenant_store, ontology, HeuristicLLMProvider()))
    empty = ManagedCandidateProfile(
        user_id="mcp",
        profile_id="mcp_default",
        profile=CandidateProfile(
            identity=CandidateIdentity(candidate_id="mcp", display_name="mcp"),
            search_profiles=(SearchProfile(),),
        ),
    )
    await runner.save_and_activate_candidate_profile("local_mcp", empty)

    positives = [
        f"Senior LLM Engineer / Python, PyTorch, RAG, Docker, Kubernetes #{idx}"
        for idx in range(10)
    ]
    for text in positives:
        result = await add_shots(
            runner,
            tenant_id="local_mcp",
            polarity="positive",
            kind="resume",
            text=text,
            profile_id="mcp_default",
        )
        assert result["ontology_errors"] == []
    jobs = [f"Hiring ML Engineer: Python, FastAPI, LLM, RAG, SQL #{idx}" for idx in range(10)]
    batched = await add_shots(
        runner,
        tenant_id="local_mcp",
        polarity="positive",
        kind="job",
        texts=jobs,
        profile_id="mcp_default",
    )
    assert batched["added"] == 10
    assert batched["ontology_errors"] == []
    assert int(batched["pos_added"]) > 0

    negatives = [f"Sales manager / accountant / recruiter only #{idx}" for idx in range(10)]
    await add_shots(
        runner,
        tenant_id="local_mcp",
        polarity="negative",
        kind="job",
        texts=negatives,
        profile_id="mcp_default",
    )
    resume_neg = [f"Java Spring enterprise accountant #{idx}" for idx in range(10)]
    last = await add_shots(
        runner,
        tenant_id="local_mcp",
        polarity="negative",
        kind="resume",
        texts=resume_neg,
        profile_id="mcp_default",
    )
    assert last["ontology_errors"] == []
    keywords = await ontology.list_positive_keywords()
    skills = await ontology.list_skills()
    assert keywords or skills
    terms = []
    for item in keywords:
        if isinstance(item, dict):
            terms.append(str(item.get("term") or ""))
        else:
            terms.append(str(item))
    blob = " ".join(terms + list(skills)).lower()
    assert "python" in blob or "llm" in blob or "rag" in blob or "pytorch" in blob


def _compiled_term(
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
        accepted=True,
    )


class _CompilerLLM:
    """Scripted classifier used by MCP refresh; model_id matches compiler default."""

    model_id = "gpt-4.1-mini"

    def __init__(self, chunk: OntologyCandidateChunk, compiled: CompiledOntology) -> None:
        self._chunk = chunk
        self._compiled = compiled

    async def classify(self, prompt: str, schema: type[Any], **kwargs: object) -> Any:
        del prompt, kwargs
        name = getattr(schema, "__name__", "")
        if "Candidate" in name:
            return self._chunk
        return self._compiled


@pytest.mark.asyncio
async def test_mcp_add_shots_compiles_roles_skills_anti_and_graph(tmp_path: Any) -> None:
    pos_text = "Senior LLM Engineer / Python, PyTorch, RAG, Docker, FastAPI"
    neg_text = "Sales manager / recruiter only, no LLM"
    pos_id = shot_id_for_text(pos_text)
    neg_id = shot_id_for_text(neg_text)
    chunk = OntologyCandidateChunk(
        terms=(
            _compiled_term(
                canonical="python",
                entity_type="skill",
                semantic_role="target_skill",
                polarity="positive",
                shot_id=pos_id,
            ),
            _compiled_term(
                canonical="llm engineer",
                entity_type="role",
                semantic_role="target_role",
                polarity="positive",
                shot_id=pos_id,
                source_section="title",
            ),
            _compiled_term(
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
        summary="mcp compile-pass",
        terms=chunk.terms,
    )
    settings = Settings(
        llm_backend="openai",
        openai_model="gpt-5.4-nano",
        ontology_compiler_model="gpt-4.1-mini",
        store_backend="sqlite",
        store_path=tmp_path / "store.db",
        tracing_enabled=False,
        openobserve_enabled=False,
        job_backend="sqlite",
        search_backend="sqlite",
        job_group_store_backend="sqlite",
        bgem3_enabled=False,
        embedding_enabled=False,
    )
    tenant_store = TenantStore("local_mcp", create_store(settings))
    ontology = create_ontology_store(settings)
    runner = _FakeRunner(
        _FakeRuntime(tenant_store, ontology, _CompilerLLM(chunk, compiled))
    )
    empty = ManagedCandidateProfile(
        user_id="mcp",
        profile_id="mcp_default",
        profile=CandidateProfile(
            identity=CandidateIdentity(candidate_id="mcp", display_name="mcp"),
            search_profiles=(SearchProfile(),),
        ),
    )
    await runner.save_and_activate_candidate_profile("local_mcp", empty)

    positive = await add_shots(
        runner,
        tenant_id="local_mcp",
        user_id="mcp",
        polarity="positive",
        kind="job",
        text=pos_text,
        profile_id="mcp_default",
    )
    assert positive["ontology_errors"] == []
    negative = await add_shots(
        runner,
        tenant_id="local_mcp",
        user_id="mcp",
        polarity="negative",
        kind="job",
        text=neg_text,
        profile_id="mcp_default",
    )
    assert negative["ontology_errors"] == []
    assert str(negative.get("model") or positive.get("model") or "") != "heuristic"

    roles = await ontology.list_roles()
    skills = await ontology.list_skills()
    anti = await ontology.list_anti_patterns()
    assert any("llm engineer" in role for role in roles)
    assert "python" in skills
    assert anti

    graph_row = await ontology._fetchone("SELECT COUNT(*) FROM jf_ontology_graph_version")
    term_row = await ontology._fetchone(
        "SELECT COUNT(*) FROM jf_ontology_compiled_term WHERE accepted = 1"
    )
    assert graph_row is not None and int(graph_row[0]) >= 1
    assert term_row is not None and int(term_row[0]) >= 1


@pytest.mark.asyncio
async def test_mcp_add_shots_compiles_full_profile_once(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        llm_backend="heuristic",
        store_backend="sqlite",
        store_path=tmp_path / "store.db",
        tracing_enabled=False,
        openobserve_enabled=False,
        job_backend="sqlite",
        search_backend="sqlite",
        job_group_store_backend="sqlite",
        bgem3_enabled=False,
        embedding_enabled=False,
    )
    tenant_store = TenantStore("local_mcp", create_store(settings))
    ontology = create_ontology_store(settings)
    runner = _FakeRunner(_FakeRuntime(tenant_store, ontology, HeuristicLLMProvider()))
    empty = ManagedCandidateProfile(
        user_id="mcp",
        profile_id="mcp_default",
        profile=CandidateProfile(
            identity=CandidateIdentity(candidate_id="mcp", display_name="mcp"),
            search_profiles=(SearchProfile(),),
        ),
    )
    await runner.save_and_activate_candidate_profile("local_mcp", empty)

    calls: list[int] = []

    async def _fake_compile(managed: ManagedCandidateProfile, **kwargs: object) -> dict[str, object]:
        del kwargs
        from job_ftch.application.profile_inputs import list_examples

        examples = list_examples(managed)
        calls.append(sum(len(values) for values in examples.values()))
        return {"pos_added": 3, "ontology_errors": [], "model": "scripted"}

    monkeypatch.setattr(
        "job_ftch.application.ontology_enrichment.compile_profile_ontology",
        _fake_compile,
    )
    result = await add_shots(
        runner,
        tenant_id="local_mcp",
        polarity="positive",
        kind="job",
        texts=[f"LLM Engineer Python #{idx}" for idx in range(10)],
        profile_id="mcp_default",
    )
    assert result["added"] == 10
    assert result["ontology_errors"] == []
    assert calls == [10]
