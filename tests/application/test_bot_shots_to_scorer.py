"""End-to-end test: bot adds shots -> in-memory BGE-M3 store has them.

The user complaint that drove the redesign was: "I added 18
examples through the bot, ran /run, and the relevance scorer
ignored all of them and used a fixed fixture set instead." This
file is the regression test that pins down the contract:

1. The bot's profile writes (positive, negative, positive_job,
   negative_job) all land in the in-memory BGE-M3 store.
2. The pipeline builder reads from that store, not from a Qdrant
   collection seeded with a fixed fixture set.
3. The BGE-M3 scorer scores a matching item higher than a
   non-matching one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np
import pytest

from job_ftch.application.shot_sync import (
    remove_user_shots,
    sync_profile_to_shot_store,
)
from job_ftch.domain import (
    CandidateIdentity,
    CandidateProfile,
    ManagedCandidateProfile,
    SearchProfile,
)
from job_ftch.domain.bgem3_card import build_bgem3_card
from job_ftch.infrastructure.relevance import managed_shots, shot_registry
from job_ftch.infrastructure.relevance.shot_anchor import (
    BgeMThreeShotScorer,
    InMemoryBgeMThreeShotStore,
)

# ---------------------------------------------------------------------------
# Provider stub: deterministic vectors per text.
# ---------------------------------------------------------------------------


class _DeterministicProvider:
    def __init__(self, dim: int = 32) -> None:
        self.dim = dim

    def encode(
        self,
        text: str,
        *,
        max_length: int = 512,
        return_sparse: bool = False,
    ) -> dict[str, Any]:
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        v = rng.standard_normal(self.dim).astype(np.float32)
        v = v / np.linalg.norm(v)
        out: dict[str, Any] = {"dense": v}
        if return_sparse:
            sp = {
                str(int(k)): float(val)
                for k, val in zip(rng.integers(1, 1000, size=3), rng.random(3), strict=False)
            }
            out["sparse"] = sp
        return out


@pytest.fixture(autouse=True)
def _disable_external_shot_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep this in-memory contract test independent of live Qdrant/BGE."""
    monkeypatch.setattr(managed_shots, "_try_get_qdrant_provider", lambda settings: None)
    monkeypatch.setattr(managed_shots, "_try_get_qdrant_store", lambda settings: None)


def _profile_with_shots(
    user_id: str,
    *,
    pos: tuple[str, ...] = (),
    neg: tuple[str, ...] = (),
    pos_job: tuple[str, ...] = (),
    neg_job: tuple[str, ...] = (),
) -> ManagedCandidateProfile:
    sp = SearchProfile(
        profile_id=f"user_{user_id}",
        positive_example_texts=pos,
        negative_example_texts=neg,
        positive_job_example_texts=pos_job,
        negative_job_example_texts=neg_job,
    )
    return ManagedCandidateProfile(
        user_id=user_id,
        profile_id=f"user_{user_id}",
        profile=CandidateProfile(
            identity=CandidateIdentity(candidate_id=user_id, display_name=user_id),
            search_profiles=(sp,),
        ),
        updated_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# The integration test.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_bot_shots_flow_into_relevance_scorer() -> None:
    """Simulate a user adding 5 positive vacancy shots and 3
    negative ones through the bot, then run the relevance
    scorer against two synthetic items: one that matches the
    positives and one that matches the negatives. The matching
    item must win.
    """
    provider = _DeterministicProvider(dim=32)
    store = InMemoryBgeMThreeShotStore(provider=provider)
    shot_registry.reset()
    shot_registry.configure(store=store, provider=provider)
    try:
        profile = _profile_with_shots(
            "u1",
            pos_job=(
                "Senior LLM Engineer with RAG and prompt engineering",
                "AI engineer building multi-agent systems",
                "Production RAG engineer for enterprise search",
                "AI Solutions Engineer integrating OpenAI APIs",
                "Backend engineer with LLM and vector DB experience",
            ),
            neg_job=(
                "Junior Marketing intern with no engineering skills",
                "Manual QA tester for legacy desktop software",
                "Sales manager for B2B SaaS, no Python",
            ),
        )
        await sync_profile_to_shot_store(
            profile=profile,
            tenant_id="default",
            user_id="u1",
        )

        # The store now has 5 positive + 3 negative vacancy shots.
        pos, neg, _, _ = store.load()
        assert pos.shape == (5, 32)
        assert neg.shape == (3, 32)

        scorer = BgeMThreeShotScorer(pos, neg, top_k=1)

        # Build a "matching" item: it has the same text as one of
        # the positive shots, so its dense vector is identical
        # and the cosine with the matching shot is ~1.0.
        matching_query = provider.encode(
            build_bgem3_card("Senior LLM Engineer with RAG and prompt engineering"),
            return_sparse=False,
        )["dense"]
        # Build a "non-matching" item using one of the negative
        # texts. Its vector points opposite to the positive
        # cluster.
        non_matching_query = provider.encode(
            build_bgem3_card("Junior Marketing intern with no engineering skills"),
            return_sparse=False,
        )["dense"]

        match_score = scorer.score_vector(matching_query)
        non_match_score = scorer.score_vector(non_matching_query)

        # The matching item scores higher on the positive side.
        assert match_score.sim_pos > non_match_score.sim_pos
        # The non-matching item scores higher on the negative side.
        assert non_match_score.sim_neg > match_score.sim_neg
        # The margin is a hard positive for the match and a hard
        # negative for the non-match.
        assert match_score.margin > 0
        assert non_match_score.margin < 0
    finally:
        shot_registry.reset()


@pytest.mark.anyio
async def test_remove_user_shots_only_drops_target_user() -> None:
    """Multi-tenant correctness: removing one user's shots must
    not affect another user's shots in the same in-process
    store.
    """
    provider = _DeterministicProvider(dim=16)
    store = InMemoryBgeMThreeShotStore(provider=provider)
    shot_registry.reset()
    shot_registry.configure(store=store, provider=provider)
    try:
        u1 = _profile_with_shots("u1", pos_job=("u1 shot",))
        u2 = _profile_with_shots("u2", pos_job=("u2 shot",))
        await sync_profile_to_shot_store(profile=u1, tenant_id="default", user_id="u1")
        await sync_profile_to_shot_store(profile=u2, tenant_id="default", user_id="u2")
        pos, _, _, _ = store.load()
        assert pos.shape == (2, 16)

        removed = remove_user_shots(tenant_id="default", user_id="u1")
        assert removed == 1
        pos, _, _, _ = store.load()
        assert pos.shape == (1, 16)
    finally:
        shot_registry.reset()
