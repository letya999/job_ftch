"""Tests for the dynamic prompt builder.

The builder calls ``llm.generate_text`` to ask the LLM to write
a per-profile relevance prompt from the user's shots. Before the
fix, this failed at runtime with ``AsyncInstructor.create() missing
'response_model'`` because the structured-output client was
incorrectly used for free-form text.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from job_ftch.application.tenant_store import TenantStore
from job_ftch.domain import (
    CandidateIdentity,
    CandidateProfile,
    ManagedCandidateProfile,
    SearchProfile,
)
from job_ftch.infrastructure.stores.in_memory import InMemoryStore


def _make_profile(
    *, pos: tuple[str, ...] = (), neg: tuple[str, ...] = ()
) -> ManagedCandidateProfile:
    sp = SearchProfile(
        profile_id="p1",
        positive_example_texts=pos,
        negative_example_texts=neg,
    )
    return ManagedCandidateProfile(
        user_id="u1",
        profile_id="user_u1",
        profile=CandidateProfile(
            identity=CandidateIdentity(candidate_id="u1", display_name="u1"),
            search_profiles=(sp,),
        ),
    )


class _StubLLM:
    """Has both ``generate_text`` (free-form) and ``classify`` (structured)."""

    def __init__(self, *, free_form: str = "GENERATED PROMPT") -> None:
        self._free_form = free_form
        self.generate_text = AsyncMock(return_value=free_form)
        self.classify = AsyncMock()


@pytest.mark.anyio
async def test_dynamic_prompt_is_generated_from_shots() -> None:
    """The full path: store a profile with shots, call the
    builder, assert the LLM was invoked and the result was
    cached.
    """
    from job_ftch.application.prompt_builder import (
        build_relevance_prompts_for_catalog,
    )
    from job_ftch.domain import ProfileCatalog

    base = InMemoryStore()
    store = TenantStore("default", base)
    llm = _StubLLM()
    profile = _make_profile(
        pos=("I want to be a senior LLM engineer",),
        neg=("I want to be a marketing intern",),
    )
    catalog = ProfileCatalog(
        catalog_name="u1",
        profiles=(profile.profile.search_profiles[0],),
    )
    prompts = await build_relevance_prompts_for_catalog(catalog, llm, store)  # type: ignore[arg-type]
    assert "p1" in prompts
    assert prompts["p1"] == "GENERATED PROMPT"
    llm.generate_text.assert_awaited_once()
    call_kwargs = llm.generate_text.await_args.kwargs
    # The user prompts must mention the actual shot text.
    assert "I want to be a senior LLM engineer" in call_kwargs["user_prompt"]
    assert "I want to be a marketing intern" in call_kwargs["user_prompt"]


@pytest.mark.anyio
async def test_dynamic_prompt_cached_until_shots_change() -> None:
    """If the user adds a new shot, the cache must invalidate.

    The cache key is a SHA-256 of the four shot buckets, so any
    change to the profile re-runs the LLM call. The bot relies
    on this to ensure the per-profile prompt always reflects the
    latest examples.
    """
    from job_ftch.application.prompt_builder import (
        build_relevance_prompts_for_catalog,
    )
    from job_ftch.domain import ProfileCatalog

    base = InMemoryStore()
    store = TenantStore("default", base)
    llm = _StubLLM()

    profile_v1 = _make_profile(pos=("first shot",))
    catalog_v1 = ProfileCatalog(
        catalog_name="u1",
        profiles=(profile_v1.profile.search_profiles[0],),
    )
    prompts_v1 = await build_relevance_prompts_for_catalog(catalog_v1, llm, store)  # type: ignore[arg-type]
    assert prompts_v1["p1"] == "GENERATED PROMPT"
    assert llm.generate_text.await_count == 1

    # Same shots -> same cache key -> no second LLM call.
    prompts_v1_again = await build_relevance_prompts_for_catalog(catalog_v1, llm, store)  # type: ignore[arg-type]
    assert prompts_v1_again["p1"] == "GENERATED PROMPT"
    assert llm.generate_text.await_count == 1

    # New shot -> cache miss -> another LLM call.
    profile_v2 = _make_profile(pos=("first shot", "second shot"))
    catalog_v2 = ProfileCatalog(
        catalog_name="u1",
        profiles=(profile_v2.profile.search_profiles[0],),
    )
    prompts_v2 = await build_relevance_prompts_for_catalog(catalog_v2, llm, store)  # type: ignore[arg-type]
    assert prompts_v2["p1"] == "GENERATED PROMPT"
    assert llm.generate_text.await_count == 2


@pytest.mark.anyio
async def test_dynamic_prompt_returns_none_when_no_shots() -> None:
    """A profile with no examples cannot drive a relevance prompt."""
    from job_ftch.application.prompt_builder import (
        build_relevance_prompts_for_catalog,
    )
    from job_ftch.domain import ProfileCatalog

    base = InMemoryStore()
    store = TenantStore("default", base)
    llm = _StubLLM()
    catalog = ProfileCatalog(
        catalog_name="empty",
        profiles=(SearchProfile(profile_id="p_empty"),),
    )
    prompts = await build_relevance_prompts_for_catalog(catalog, llm, store)  # type: ignore[arg-type]
    assert prompts["p_empty"] is None
    llm.generate_text.assert_not_called()


@pytest.mark.anyio
async def test_decision_brief_cache_includes_ontology_hash() -> None:
    from job_ftch.application.prompt_builder import DecisionProfileBriefCompiler

    store = TenantStore("default", InMemoryStore())
    llm = _StubLLM()
    profile = _make_profile(pos=("LLM engineer",)).profile.search_profiles[0]
    compiler = DecisionProfileBriefCompiler(llm, store)  # type: ignore[arg-type]

    first = await compiler.compile(profile, ontology_snapshot_hash="ontology-a")
    cached = await compiler.compile(profile, ontology_snapshot_hash="ontology-a")
    changed = await compiler.compile(profile, ontology_snapshot_hash="ontology-b")

    assert first is not None and cached is not None and changed is not None
    assert first.input_hash == cached.input_hash
    assert first.input_hash != changed.input_hash
    assert llm.generate_text.await_count == 2
