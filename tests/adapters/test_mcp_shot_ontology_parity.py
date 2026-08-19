"""MCP shot ingest must populate ontology tables like the Telegram bot path."""

from __future__ import annotations

from typing import Any

import pytest

from job_ftch.adapters.mcp.product_surface import add_shots
from job_ftch.application.registry import create_ontology_store, create_store
from job_ftch.application.tenant_store import TenantStore
from job_ftch.config import Settings
from job_ftch.domain import (
    CandidateIdentity,
    CandidateProfile,
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
        text="Data Scientist with pandas, scikit-learn, SQL and A/B testing only",
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
