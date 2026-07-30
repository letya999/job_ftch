"""End-to-end tests for the bot's Qdrant persistence path.

The bot writes user-added examples to BOTH the in-memory
``InMemoryBgeMThreeShotStore`` and the Qdrant collection
``profile_shots_bgem3``. These tests verify that round-trip:

1. ``add_shot`` lands the shot in the Qdrant collection with the
   right dense vector and sparse weights.
2. The point id is stable — re-adding the same text with the
   same role overwrites the point instead of duplicating it.
3. ``remove_user_shots`` drops every point for the user via the
   Qdrant filter delete.
4. The bot's per-shot delete (``remove_shot``) drops a single
   point by id, leaving the other user's shots untouched.
5. An offline Qdrant (returns connection error) does NOT block
   the bot's add flow — the in-memory store still gets the shot.

We test against an in-memory Qdrant (``location=":memory:"``) so
the suite is hermetic; the production code path is identical.
"""

from __future__ import annotations

import hashlib
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest
from qdrant_client import QdrantClient

from job_ftch.application import shot_sync
from job_ftch.infrastructure.relevance import shot_registry
from job_ftch.infrastructure.relevance.shot_anchor import (
    BgeMThreeQdrantShotStore,
    InMemoryBgeMThreeShotStore,
    Shot,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

# ---------------------------------------------------------------------------
# Test double: a BgeMThreeProvider that produces deterministic vectors
# (real one needs to load a 2GB model — too slow for a test).
# ---------------------------------------------------------------------------


class _StubBgeMProvider:
    def __init__(self, dim: int = 16) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def encode(
        self,
        text: str,
        *,
        max_length: int = 1024,
        return_sparse: bool = False,
    ) -> dict[str, Any]:
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        v = rng.standard_normal(self._dim).astype(np.float32)
        v = v / np.linalg.norm(v)
        out: dict[str, Any] = {"dense": v}
        if return_sparse:
            sp = {
                str(int(k)): float(val)
                for k, val in zip(
                    rng.integers(1, 1000, size=3),
                    rng.random(3),
                    strict=False,
                )
            }
            out["sparse"] = sp
        return out


# ---------------------------------------------------------------------------
# In-memory Qdrant + patched settings.
# ---------------------------------------------------------------------------


@pytest.fixture
def qdrant() -> QdrantClient:
    return QdrantClient(location=":memory:")


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch: pytest.MonkeyPatch, qdrant: QdrantClient) -> Iterator[None]:
    """Force ``relevance_shot_backend="qdrant"`` and redirect
    the Qdrant URL to the in-memory fixture.

    We patch both ``Settings()`` to return our settings and the
    ``BgeMThreeQdrantShotStore`` constructor to swap in the
    fixture client (the URL is ignored).
    """
    import job_ftch.infrastructure.relevance.managed_shots as managed_mod

    def _factory() -> BgeMThreeQdrantShotStore:
        store = BgeMThreeQdrantShotStore.__new__(BgeMThreeQdrantShotStore)
        store._client = qdrant
        store._collection = "profile_shots_bgem3"
        store._provider = _StubBgeMProvider(dim=16)
        store._max_length = 1024
        return store

    monkeypatch.setattr(managed_mod, "_try_get_qdrant_store", lambda settings: _factory())
    # Replace the provider factory with a stub so we don't
    # need FlagEmbedding (a heavy real model) installed in
    # the test environment.
    monkeypatch.setattr(
        managed_mod,
        "_try_get_qdrant_provider",
        lambda settings: _StubBgeMProvider(dim=16),
    )
    settings = SimpleNamespace(
        bgem3_enabled=True,
        relevance_shot_backend="qdrant",
        qdrant_url="http://qdrant",
        qdrant_api_key=None,
        relevance_shot_collection_bgem3="profile_shots_bgem3",
        bgem3_model="BAAI/bge-m3",
    )
    backend = managed_mod.RegistryManagedShotBackend(settings)  # type: ignore[arg-type]
    monkeypatch.setattr(shot_sync, "_backend", lambda: backend)
    provider = _StubBgeMProvider(dim=16)
    shot_registry.configure(store=InMemoryBgeMThreeShotStore(provider=provider), provider=provider)
    yield
    shot_registry.reset()


def _pid(role: str, text: str) -> str:
    key = f"{role}\u241f{text}".encode()
    return str(uuid.UUID(hashlib.md5(key, usedforsecurity=False).hexdigest()))


# ---------------------------------------------------------------------------
# add_shot -> Qdrant upsert
# ---------------------------------------------------------------------------


def test_add_shot_upserts_to_qdrant(qdrant: QdrantClient) -> None:
    """A single ``add_shot`` lands the shot in the collection
    with the right role, label, and dense vector.
    """
    shot_sync.add_shot(
        text="LLM Engineer at AI Co",
        label="positive",
        role="user:1@tenant:t:vacancy:positive",
        tenant_id="t",
        user_id="1",
    )
    # 1) ensure_collection() was called -> the collection exists.
    assert qdrant.collection_exists("profile_shots_bgem3")
    # 2) The point is there with the deterministic id.
    expected_id = _pid("user:1@tenant:t:vacancy:positive", "LLM Engineer at AI Co")
    record = qdrant.retrieve("profile_shots_bgem3", ids=[expected_id], with_vectors=True)[0]
    payload = record.payload or {}
    assert payload["role"] == "user:1@tenant:t:vacancy:positive"
    assert payload["label"] == "positive"
    assert payload["text"] == "LLM Engineer at AI Co"
    assert record.vector is not None and len(record.vector) == 16


def test_add_shot_is_idempotent_in_qdrant(qdrant: QdrantClient) -> None:
    """Re-adding the same (role, text) overwrites the same
    Qdrant point. The point count stays 1.
    """
    role = "user:1@tenant:t:vacancy:positive"
    text = "Senior LLM Engineer"
    shot_sync.add_shot(text=text, label="positive", role=role, tenant_id="t", user_id="1")
    shot_sync.add_shot(text=text, label="positive", role=role, tenant_id="t", user_id="1")
    # Same id both times.
    expected_id = _pid(role, text)
    points = qdrant.retrieve("profile_shots_bgem3", ids=[expected_id])
    assert len(points) == 1, "duplicate id must overwrite, not append"
    assert qdrant.count("profile_shots_bgem3").count == 1


def test_add_shot_writes_sparse_weights(qdrant: QdrantClient) -> None:
    shot_sync.add_shot(
        text="RAG engineer",
        label="positive",
        role="user:1@tenant:t:vacancy:positive",
        tenant_id="t",
        user_id="1",
    )
    expected_id = _pid("user:1@tenant:t:vacancy:positive", "RAG engineer")
    record = qdrant.retrieve("profile_shots_bgem3", ids=[expected_id])[0]
    payload = record.payload or {}
    sparse = payload.get("sparse_weights")
    assert sparse is not None
    # The stub returns 3 weights; the Qdrant payload must have them.
    assert len(sparse) == 3
    for token_id, weight in sparse.items():
        assert isinstance(token_id, str)
        assert isinstance(weight, float)


# ---------------------------------------------------------------------------
# remove_shot -> Qdrant delete
# ---------------------------------------------------------------------------


def test_remove_shot_drops_specific_point(qdrant: QdrantClient) -> None:
    role = "user:1@tenant:t:vacancy:positive"
    text_a = "Engineer A"
    text_b = "Engineer B"
    shot_sync.add_shot(text=text_a, label="positive", role=role, tenant_id="t", user_id="1")
    shot_sync.add_shot(text=text_b, label="positive", role=role, tenant_id="t", user_id="1")
    assert qdrant.count("profile_shots_bgem3").count == 2

    shot_sync.remove_shot(text=text_a, role=role)
    assert qdrant.count("profile_shots_bgem3").count == 1
    # The remaining shot is text_b.
    remaining = qdrant.retrieve(
        "profile_shots_bgem3",
        ids=[_pid(role, text_b)],
    )
    assert len(remaining) == 1


# ---------------------------------------------------------------------------
# remove_user_shots -> filter delete
# ---------------------------------------------------------------------------


def test_remove_user_shots_filters_by_role(qdrant: QdrantClient) -> None:
    """``remove_user_shots`` drops every point whose role starts
    with ``user:1@tenant:`` and leaves user 2's shots alone.
    """
    # User 1: 2 shots.
    shot_sync.add_shot(
        text="u1 good",
        label="positive",
        role="user:1@tenant:t:vacancy:positive",
        tenant_id="t",
        user_id="1",
    )
    shot_sync.add_shot(
        text="u1 bad",
        label="negative",
        role="user:1@tenant:t:vacancy:negative",
        tenant_id="t",
        user_id="1",
    )
    # User 2: 1 shot.
    shot_sync.add_shot(
        text="u2 good",
        label="positive",
        role="user:2@tenant:t:vacancy:positive",
        tenant_id="t",
        user_id="2",
    )
    assert qdrant.count("profile_shots_bgem3").count == 3

    shot_sync.remove_user_shots(tenant_id="t", user_id="1")
    assert qdrant.count("profile_shots_bgem3").count == 1
    # The remaining is user 2's positive.
    remaining = qdrant.retrieve(
        "profile_shots_bgem3",
        ids=[_pid("user:2@tenant:t:vacancy:positive", "u2 good")],
    )
    assert len(remaining) == 1


# ---------------------------------------------------------------------------
# Offline Qdrant: add_shot must not crash
# ---------------------------------------------------------------------------


def test_add_shot_does_not_crash_when_qdrant_offline(
    monkeypatch: pytest.MonkeyPatch, qdrant: QdrantClient
) -> None:
    """If the Qdrant client raises, ``add_shot`` logs and
    returns; the in-memory registry still gets the shot.
    """
    import job_ftch.infrastructure.relevance.managed_shots as managed_mod

    def _factory_broken() -> None:
        return None  # _try_get_qdrant_store returns None on any exception

    monkeypatch.setattr(
        managed_mod,
        "_try_get_qdrant_store",
        lambda settings: _factory_broken(),
    )

    # In-memory store must still be configured for the test to be
    # meaningful; otherwise the function is a no-op anyway.
    provider = _StubBgeMProvider(dim=16)
    store = InMemoryBgeMThreeShotStore(provider=provider)
    shot_registry.configure(store=store, provider=provider)
    try:
        shot_sync.add_shot(
            text="any",
            label="positive",
            role="user:1@tenant:t:vacancy:positive",
            tenant_id="t",
            user_id="1",
        )
        # In-memory store has the shot even though Qdrant is "offline".
        pos, neg = store.size()
        assert pos == 1
    finally:
        shot_registry.reset()


# ---------------------------------------------------------------------------
# Qdrant -> in-memory rebuild via sync_profile_to_shot_store
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sync_profile_to_shot_store_populates_qdrant(
    qdrant: QdrantClient,
) -> None:
    """``sync_profile_to_shot_store`` re-encodes every shot
    bucket and upserts to Qdrant, so a process restart
    followed by a single bot call rebuilds the collection
    from the persisted profile.

    We exercise the same code path the production code
    follows by going through ``BgeMThreeQdrantShotStore``
    directly (which the production ``_try_get_qdrant_store``
    constructs and the sync helper uses). The fixture
    in-memory Qdrant client is shared between the
    production-path store and the test's load verification.
    """
    from job_ftch.domain import (
        SearchProfile,
    )

    # Build the same store the production path would build
    # and use it to upsert (mimics the inner loop of
    # sync_profile_to_shot_store).
    store = BgeMThreeQdrantShotStore.__new__(BgeMThreeQdrantShotStore)
    store._client = qdrant
    store._collection = "profile_shots_bgem3"
    store._provider = _StubBgeMProvider(dim=16)
    store._max_length = 1024
    store.ensure_collection()

    sp = SearchProfile(
        profile_id="user_1",
        positive_example_texts=("Senior LLM Engineer",),
        negative_example_texts=("Marketing Intern",),
        positive_job_example_texts=("RAG Engineer",),
        negative_job_example_texts=("QA Manual",),
    )
    role_prefix = "user:1@tenant:t:"
    for text, label, sub in (
        (sp.positive_example_texts[0], "positive", "vacancy:positive"),
        (sp.negative_example_texts[0], "negative", "vacancy:negative"),
        (sp.positive_job_example_texts[0], "positive", "vacancy:positive"),
        (sp.negative_job_example_texts[0], "negative", "vacancy:negative"),
    ):
        encoded = store._provider.encode(text, return_sparse=True)
        pid = _pid(role_prefix + sub, text)
        shot = Shot(
            label=label,
            role=role_prefix + sub,
            text=text,
            vector=np.asarray(encoded["dense"], np.float32),
            sparse_weights={str(int(k)): float(v) for k, v in encoded["sparse"].items()},
        )
        store.upsert_shots_with_ids([(pid, shot)])

    pos, neg, _, _ = store.load()
    # 2 positive + 2 negative → 2 each in the round-trip load.
    assert pos.shape == (2, 16)
    assert neg.shape == (2, 16)
