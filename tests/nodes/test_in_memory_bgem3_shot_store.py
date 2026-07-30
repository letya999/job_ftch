"""Tests for the in-memory BGE-M3 shot store.

The store is the source of truth for what the relevance scorer sees
at pipeline time. These tests pin down the contract: a shot added
through ``add_text`` is encoded with the live provider, dim
mismatches are caught loudly, and the load() output is consumable
by the BGE-M3 scorer.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

from job_ftch.domain import (
    CandidateIdentity,
    CandidateProfile,
    ManagedCandidateProfile,
    SearchProfile,
)
from job_ftch.infrastructure.relevance import shot_registry
from job_ftch.infrastructure.relevance.shot_anchor import (
    BgeMThreeShotScorer,
    InMemoryBgeMThreeShotStore,
    Shot,
)

# ---------------------------------------------------------------------------
# Test double for the BGE-M3 provider.
# ---------------------------------------------------------------------------


class _StubProvider:
    """Deterministic encoder. Each call hashes the text into a
    fixed-length vector so dim stays consistent across the test
    session. Returning the same shape for the same text lets the
    store's "reuse existing shot" logic be exercised cheaply.
    """

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim
        self.calls: list[str] = []

    def encode(
        self,
        text: str,
        *,
        max_length: int = 512,
        return_sparse: bool = False,
    ) -> dict:
        self.calls.append(text)
        # Stable per-text deterministic 1024-dim vector.
        seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
        rng = np.random.default_rng(seed)
        dense = rng.standard_normal(self.dim).astype(np.float32)
        # Normalize so cosine is well-behaved.
        dense = dense / np.linalg.norm(dense)
        out: dict = {"dense": dense}
        if return_sparse:
            # Stable per-text sparse weights.
            token_ids = rng.choice(np.arange(1, 1000), size=5, replace=False)
            sp = {
                str(int(k)): float(v)
                for k, v in zip(
                    token_ids,
                    rng.random(5),
                    strict=True,
                )
            }
            out["sparse"] = sp
        return out


@pytest.fixture
def provider() -> _StubProvider:
    return _StubProvider(dim=1024)


@pytest.fixture
def store(provider: _StubProvider) -> InMemoryBgeMThreeShotStore:
    return InMemoryBgeMThreeShotStore(provider=provider)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# add_text: dim check + sparse coercion
# ---------------------------------------------------------------------------


def test_add_text_encodes_dense_and_sparse(store: InMemoryBgeMThreeShotStore) -> None:
    """The default flow is bot->store, which encodes eagerly."""
    shot = store.add_text("hello world", label="positive", role="resume:positive")
    assert shot.label == "positive"
    assert shot.text == "hello world"
    assert shot.role == "resume:positive"
    assert shot.vector.shape == (1024,)
    assert shot.sparse_weights is not None
    assert len(shot.sparse_weights) == 5


def test_add_text_rejects_empty_text(store: InMemoryBgeMThreeShotStore) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        store.add_text("", label="positive")


def test_add_text_rejects_invalid_label(store: InMemoryBgeMThreeShotStore) -> None:
    with pytest.raises(ValueError, match="label must be"):
        store.add_text("text", label="unknown")


def test_add_shot_validates_dim_mismatch(provider: _StubProvider) -> None:
    """A shot with the wrong vector dim must fail loudly, not be
    silently dropped. This protects against the Qdrant-dim bug
    that previously corrupted the relevance score.
    """
    store = InMemoryBgeMThreeShotStore(provider=provider)  # type: ignore[arg-type]
    bad_shot = Shot(
        label="positive",
        role="resume:positive",
        text="text",
        vector=np.zeros(512, dtype=np.float32),  # 512 != 1024
    )
    with pytest.raises(ValueError, match="dim 512 does not match provider dim 1024"):
        store.add_shot(bad_shot)


# ---------------------------------------------------------------------------
# load: per-label split + correct dim
# ---------------------------------------------------------------------------


def test_load_returns_split_dense_sparse(
    store: InMemoryBgeMThreeShotStore,
) -> None:
    store.add_text("good", label="positive", role="resume:positive")
    store.add_text("bad", label="negative", role="resume:negative")
    pos_d, neg_d, pos_s, neg_s = store.load()
    assert pos_d.shape == (1, 1024)
    assert neg_d.shape == (1, 1024)
    assert len(pos_s) == 1
    assert len(neg_s) == 1


def test_load_empty_returns_zero_dim_matrices(
    store: InMemoryBgeMThreeShotStore,
) -> None:
    pos, neg, pos_s, neg_s = store.load()
    assert pos.shape == (0, 1024)
    assert neg.shape == (0, 1024)
    assert pos_s == []
    assert neg_s == []


def test_in_memory_load_filters_by_tenant(store: InMemoryBgeMThreeShotStore) -> None:
    store.add_text(
        "tenant a positive",
        label="positive",
        role="user:u1@tenant:tenant_a:vacancy:positive",
    )
    store.add_text(
        "tenant b negative",
        label="negative",
        role="user:u2@tenant:tenant_b:vacancy:negative",
    )

    pos, neg, _, _ = store.load(tenant_id="tenant_a")

    assert pos.shape == (1, 1024)
    assert neg.shape == (0, 1024)


def test_in_memory_load_no_filter_returns_all(store: InMemoryBgeMThreeShotStore) -> None:
    store.add_text(
        "tenant a positive",
        label="positive",
        role="user:u1@tenant:tenant_a:vacancy:positive",
    )
    store.add_text(
        "tenant b negative",
        label="negative",
        role="user:u2@tenant:tenant_b:vacancy:negative",
    )

    pos, neg, _, _ = store.load()

    assert pos.shape == (1, 1024)
    assert neg.shape == (1, 1024)


def test_size_counts_labels(store: InMemoryBgeMThreeShotStore) -> None:
    store.add_text("p1", label="positive")
    store.add_text("p2", label="positive")
    store.add_text("n1", label="negative")
    assert store.size() == (2, 1)


# ---------------------------------------------------------------------------
# clear / clear_by_role / remove_text
# ---------------------------------------------------------------------------


def test_remove_text_drops_matching_shots(
    store: InMemoryBgeMThreeShotStore,
) -> None:
    store.add_text("hello", label="positive")
    store.add_text("hello", label="negative")
    store.add_text("world", label="positive")
    removed = store.remove_text("hello")
    assert removed == 2
    pos, neg, _, _ = store.load()
    assert pos.shape == (1, 1024)
    assert neg.shape == (0, 1024)


def test_clear_by_role_prefix(store: InMemoryBgeMThreeShotStore) -> None:
    store.add_text("r1", label="positive", role="user:42:resume:positive")
    store.add_text("r2", label="positive", role="user:42:vacancy:positive")
    store.add_text("r3", label="positive", role="user:99:resume:positive")
    removed = store.clear_by_role("user:42:")
    assert removed == 2
    pos, _, _, _ = store.load()
    assert pos.shape == (1, 1024)


# ---------------------------------------------------------------------------
# replace_from_profiles: round-trip from SearchProfile list
# ---------------------------------------------------------------------------


def test_replace_from_profiles_uses_all_four_buckets(
    store: InMemoryBgeMThreeShotStore,
) -> None:
    sp = SearchProfile(
        profile_id="p1",
        positive_example_texts=("good resume",),
        negative_example_texts=("bad resume",),
        positive_job_example_texts=("good job",),
        negative_job_example_texts=("bad job",),
    )
    counts = store.size()
    assert counts == (0, 0)
    store.replace_from_profiles([sp])
    p, n = store.size()
    assert p == 2
    assert n == 2
    pos, neg, _, _ = store.load()
    assert pos.shape == (2, 1024)
    assert neg.shape == (2, 1024)


def test_replace_from_profiles_reuses_existing_vectors(
    store: InMemoryBgeMThreeShotStore,
    provider: _StubProvider,
) -> None:
    """If a shot with the same (role, text) is already in the store
    we re-use it instead of re-encoding. This matters for pipelines
    that call ``replace_from_profiles`` on every run: without
    reuse, every /run would re-encode all 18 user shots and the
    relevance gate would burn 18 * 1024-dim encodes for no gain.
    """
    sp = SearchProfile(
        profile_id="p1",
        positive_example_texts=("stable text",),
    )
    store.replace_from_profiles([sp])
    calls_after_first = len(provider.calls)
    # Second replace with the same profile should NOT re-encode.
    store.replace_from_profiles([sp])
    assert len(provider.calls) == calls_after_first


# ---------------------------------------------------------------------------
# Compatibility with the BGE-M3 scorer.
# ---------------------------------------------------------------------------


def test_store_output_is_usable_by_bgem3_scorer(
    store: InMemoryBgeMThreeShotStore,
) -> None:
    """The pipeline contract: ``store.load()`` produces dense matrices
    + sparse dicts that ``BgeMThreeShotScorer`` accepts directly.
    """
    store.add_text("llm engineer", label="positive", role="resume:positive")
    store.add_text("data scientist", label="negative", role="resume:negative")
    pos, neg, pos_s, neg_s = store.load()
    scorer = BgeMThreeShotScorer(pos, neg, pos_sparse=pos_s, neg_sparse=neg_s)
    # A query vector that matches the positive should score high.
    pos_query = pos[0]
    score = scorer.score_vector(pos_query)
    assert score.sim_pos > 0.5, (
        f"expected positive query to match positive shot, got {score.sim_pos}"
    )
    # A query that matches the negative should score low on pos.
    neg_query = neg[0]
    score_neg = scorer.score_vector(neg_query)
    assert score_neg.sim_pos < score.sim_pos, "negative query must score lower on positive side"


# ---------------------------------------------------------------------------
# Category selection and registry binding.
# ---------------------------------------------------------------------------


def test_load_can_select_vacancy_shots_without_mixing_resume_anchors(
    provider: _StubProvider,
) -> None:
    store = InMemoryBgeMThreeShotStore(provider=provider)  # type: ignore[arg-type]
    store.add_text("resume positive", label="positive", role="resume:positive")
    store.add_text("vacancy positive", label="positive", role="vacancy:positive")
    store.add_text("vacancy negative", label="negative", role="vacancy:negative")

    positives, negatives, _, _ = store.load(categories=("vacancy",))

    assert positives.shape[0] == 1
    assert negatives.shape[0] == 1


def test_shot_registry_round_trip(provider: _StubProvider) -> None:
    store = InMemoryBgeMThreeShotStore(provider=provider)  # type: ignore[arg-type]
    shot_registry.reset()
    shot_registry.configure(store=store, provider=provider)  # type: ignore[arg-type]
    try:
        assert shot_registry.get_store() is store
        assert shot_registry.get_provider() is provider
    finally:
        shot_registry.reset()
    assert shot_registry.get_store() is None
    assert shot_registry.get_provider() is None


# ---------------------------------------------------------------------------
# Round-trip: profile -> store -> scorer.
# ---------------------------------------------------------------------------


def test_end_to_end_profile_to_scorer(
    provider: _StubProvider,
) -> None:
    """The whole reason the in-memory store exists: a SearchProfile
    derived from the user's bot interactions must drive the
    relevance scorer. This is the regression test for the bug
    where the production pipeline ran against a fixed Qdrant
    fixture set instead of the user's actual shots.
    """
    from job_ftch.application.shot_sync import sync_profile_to_shot_store

    store = InMemoryBgeMThreeShotStore(provider=provider)  # type: ignore[arg-type]
    shot_registry.configure(store=store, provider=provider)  # type: ignore[arg-type]
    try:
        profile = ManagedCandidateProfile(
            user_id="u1",
            profile_id="user_u1",
            profile=CandidateProfile(
                identity=CandidateIdentity(candidate_id="u1", display_name="u1"),
                search_profiles=(
                    SearchProfile(
                        profile_id="user_u1",
                        positive_example_texts=("LLM Engineer at AI platform",),
                        negative_example_texts=("Junior Marketing Intern",),
                        positive_job_example_texts=("Senior AI Engineer",),
                        negative_job_example_texts=("Manual QA tester",),
                    ),
                ),
            ),
        )
        # Mimic the bot calling sync_profile_to_shot_store.
        import asyncio

        asyncio.run(
            sync_profile_to_shot_store(
                profile=profile,
                tenant_id="default",
                user_id="u1",
            )
        )
        pos, neg, pos_s, neg_s = store.load()
        # The 4 text slots become 2 positive shots and 2 negative shots.
        assert pos.shape == (2, 1024)
        assert neg.shape == (2, 1024)
        # Role prefix isolates this user.
        for shot in store._shots:  # type: ignore[attr-defined]
            assert shot.role.startswith("user:u1@tenant:default:")
    finally:
        shot_registry.reset()


# ---------------------------------------------------------------------------
# Static ``BgeMThreeShotStore`` symbol remains importable for legacy
# scripts (seed_all_shots.py uses it as ``BgeMThreeShotStore``).
# ---------------------------------------------------------------------------


def test_legacy_alias_is_qdrant_store() -> None:
    from job_ftch.infrastructure.relevance import shot_anchor

    cls = getattr(shot_anchor, "BgeMThreeShotStore", None)
    assert cls is not None
    assert cls is shot_anchor.BgeMThreeQdrantShotStore
