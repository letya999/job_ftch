"""Tests for the Qdrant-backed BGE-M3 shot store.

The default ``relevance_shot_backend`` is now ``"qdrant"`` so the
bot's user-added examples persist across process restarts. These
tests pin down the contract:

1. ``upsert_shots_with_ids`` is idempotent — a re-upsert of the
   same (role, text) overwrites the existing point instead of
   accumulating duplicates.
2. ``delete_shots_by_ids`` removes specific points.
3. ``delete_by_role_prefix`` removes every point for one user
   (used by the bot's "clear all" flow).
4. ``ensure_collection`` recreates the collection when the dim
   changes (model swap) so a stale fixture collection cannot
   silently corrupt the relevance score.
5. Round-trip: load() returns dense matrices of the right shape
   and the right dim after upsert.
6. Sparse weights round-trip through Qdrant payload.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import numpy as np
import pytest
from qdrant_client import QdrantClient
from qdrant_client.http import models as rest

from job_ftch.infrastructure.relevance.shot_anchor import (
    BgeMThreeQdrantShotStore,
    BgeMThreeShotScorer,
    Shot,
)


def _pid(name: str) -> str:
    """Deterministic UUID derived from a friendly id.

    Qdrant in-memory mode requires point ids to be valid UUIDs;
    the production code already passes UUID5 hashes (see
    ``shot_sync._qdrant_id_for``) so this helper is just a
    convenience for tests.
    """
    digest = hashlib.md5(name.encode("utf-8"), usedforsecurity=False).hexdigest()
    return str(uuid.UUID(digest))


# ---------------------------------------------------------------------------
# Test double: a real BGE-M3 provider shape with controllable dim.
# We don't load the real model — too slow for a test. The
# ``encode`` method returns a deterministic dense+sparse vector
# at the provider's dim.
# ---------------------------------------------------------------------------


class _StubProvider:
    def __init__(self, dim: int = 16) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def encode(
        self,
        text: str,
        *,
        max_length: int = 512,
        return_sparse: bool = False,
    ) -> dict:
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        v = rng.standard_normal(self._dim).astype(np.float32)
        v = v / np.linalg.norm(v)
        out: dict = {"dense": v}
        if return_sparse:
            sp = {
                str(int(k)): float(val)
                for k, val in zip(rng.integers(1, 1000, size=3), rng.random(3), strict=False)
            }
            out["sparse"] = sp
        return out


# ---------------------------------------------------------------------------
# In-memory Qdrant fixture. Same API as a real one — works without
# docker / network.
# ---------------------------------------------------------------------------


@pytest.fixture
def qdrant() -> QdrantClient:
    return QdrantClient(location=":memory:")


@pytest.fixture
def store(qdrant: QdrantClient) -> BgeMThreeQdrantShotStore:
    """Build a store whose Qdrant client is the in-memory fixture.

    We bypass the URL constructor (which rejects ``:memory:``
    schemes) and inject the fixture client directly. The
    collection name and provider are real.
    """
    store = BgeMThreeQdrantShotStore.__new__(BgeMThreeQdrantShotStore)
    store._client = qdrant
    store._collection = "profile_shots_bgem3"
    store._provider = _StubProvider(dim=16)
    store._max_length = 1024
    return store


# We need to inject the in-memory Qdrant client into the store.
@pytest.fixture
def wired_store(store: BgeMThreeQdrantShotStore, qdrant: QdrantClient) -> BgeMThreeQdrantShotStore:
    store._client = qdrant  # type: ignore[attr-defined]
    store.ensure_collection()
    return store


# ---------------------------------------------------------------------------
# upsert_shots_with_ids
# ---------------------------------------------------------------------------


def test_upsert_with_ids_creates_points(wired_store: BgeMThreeQdrantShotStore) -> None:
    """A single upsert creates one point per (id, shot) pair."""
    shot = Shot(
        label="positive",
        role="user:1@tenant:t:vacancy:positive",
        text="LLM Engineer at AI Co",
        vector=np.ones(16, dtype=np.float32),
        sparse_weights={"42": 1.0},
    )
    n = wired_store.upsert_shots_with_ids([(_pid("point-1"), shot)])
    assert n == 1
    pos, neg, pos_s, neg_s = wired_store.load()
    assert pos.shape == (1, 16)
    assert neg.shape == (0, 16)


def test_upsert_with_ids_is_idempotent(wired_store: BgeMThreeQdrantShotStore) -> None:
    """Re-upserting the same id overwrites the point, not duplicates it.

    This is the contract the bot relies on when a user re-sends
    the same example: the second add must not produce two
    relevance-scoring anchors for the same text.

    Qdrant normalises cosine vectors on insert, so the stored
    vector is not bit-equal to what we passed in. The check
    is therefore: the row count stays 1, and the upserted
    shot's *direction* (sign) matches the loaded vector.
    """
    shot_v1 = Shot(
        label="positive",
        role="user:1@tenant:t:vacancy:positive",
        text="LLM Engineer",
        vector=np.ones(16, dtype=np.float32),
    )
    shot_v2 = Shot(
        label="positive",
        role="user:1@tenant:t:vacancy:positive",
        text="LLM Engineer (revised)",
        vector=np.full(16, 2.0, dtype=np.float32),
    )
    wired_store.upsert_shots_with_ids([(_pid("same-id"), shot_v1)])
    wired_store.upsert_shots_with_ids([(_pid("same-id"), shot_v2)])

    pos, neg, _, _ = wired_store.load()
    assert pos.shape == (1, 16), "second upsert must overwrite, not append"
    # Stored vector is v2 normalised. Compare sign-by-sign.
    expected_v2 = shot_v2.vector / np.linalg.norm(shot_v2.vector)
    np.testing.assert_allclose(pos[0], expected_v2, atol=1e-5)


def test_load_supports_legacy_tenant_scoping_from_role_only(
    wired_store: BgeMThreeQdrantShotStore,
) -> None:
    """Tenant-scoped load must keep seeing pre-migration points.

    Older Qdrant shots stored the tenant only inside ``role`` and had no
    dedicated ``tenant_id`` payload field. New tenant isolation must keep
    those points visible, otherwise the scorer can end up with only one side
    of the positive/negative contrast and collapse precision.
    """
    legacy_positive = Shot(
        label="positive",
        role="user:480637186@tenant:ai_jobs:vacancy:positive",
        text="good legacy",
        vector=np.ones(16, dtype=np.float32),
    )
    legacy_negative = Shot(
        label="negative",
        role="user:480637186@tenant:ai_jobs:vacancy:negative",
        text="bad legacy",
        vector=np.full(16, 0.5, dtype=np.float32),
    )
    wired_store.upsert_shots_with_ids(
        [(_pid("legacy-pos"), legacy_positive), (_pid("legacy-neg"), legacy_negative)]
    )

    records, _ = wired_store._client.scroll(  # type: ignore[attr-defined]
        collection_name=wired_store._collection,  # type: ignore[attr-defined]
        with_payload=True,
        with_vectors=False,
        limit=10_000,
    )
    for rec in records:
        payload = dict(rec.payload or {})
        if payload.get("role") == legacy_positive.role:
            payload.pop("tenant_id", None)
            payload.pop("user_id", None)
            wired_store._client.set_payload(  # type: ignore[attr-defined]
                collection_name=wired_store._collection,  # type: ignore[attr-defined]
                payload=payload,
                points=[rec.id],
            )
        if payload.get("role") == legacy_negative.role:
            payload.pop("tenant_id", None)
            payload.pop("user_id", None)
            wired_store._client.set_payload(  # type: ignore[attr-defined]
                collection_name=wired_store._collection,  # type: ignore[attr-defined]
                payload=payload,
                points=[rec.id],
            )

    pos, neg, _, _ = wired_store.load(tenant_id="ai_jobs")
    assert pos.shape == (1, 16)
    assert neg.shape == (1, 16)


# ---------------------------------------------------------------------------
# delete_shots_by_ids
# ---------------------------------------------------------------------------


def test_delete_by_ids_removes_specific_points(
    wired_store: BgeMThreeQdrantShotStore,
) -> None:
    s1 = Shot(
        label="positive",
        role="user:1:resume:positive",
        text="good",
        vector=np.ones(16, dtype=np.float32),
    )
    s2 = Shot(
        label="positive",
        role="user:1:vacancy:positive",
        text="also good",
        vector=np.full(16, 3.0, dtype=np.float32),
    )
    wired_store.upsert_shots_with_ids([(_pid("p1"), s1), (_pid("p2"), s2)])

    removed = wired_store.delete_shots_by_ids([_pid("p1")])
    assert removed == 1

    pos, neg, _, _ = wired_store.load()
    assert pos.shape == (1, 16)
    # p2 is still there. Stored vector is the original
    # normalised; compare against the normalised p2 vector.
    expected = s2.vector / np.linalg.norm(s2.vector)
    np.testing.assert_allclose(pos[0], expected, atol=1e-5)


def test_delete_by_ids_empty_input_is_safe(
    wired_store: BgeMThreeQdrantShotStore,
) -> None:
    # No ids at all.
    assert wired_store.delete_shots_by_ids([]) == 0


def test_delete_by_ids_unknown_id_is_safe(
    wired_store: BgeMThreeQdrantShotStore,
) -> None:
    """Deleting an id that does not exist must not raise."""
    s = Shot(label="positive", role="r", text="t", vector=np.ones(16, np.float32))
    wired_store.upsert_shots_with_ids([(_pid("real"), s)])
    # Delete the real one first.
    wired_store.delete_shots_by_ids([_pid("real")])
    # Now delete a non-existent id (already gone) — must be safe.
    result = wired_store.delete_shots_by_ids([_pid("never-existed")])
    assert result == 1  # Qdrant reports the call as "1 attempted"


# ---------------------------------------------------------------------------
# delete_by_role_prefix
# ---------------------------------------------------------------------------


def test_delete_by_role_prefix_clears_one_user(
    wired_store: BgeMThreeQdrantShotStore,
) -> None:
    """The bot's /clear flow drops every shot for a user while
    leaving other users' shots untouched.
    """
    # User 1: 2 shots.
    wired_store.upsert_shots_with_ids(
        [
            (
                _pid("u1-1"),
                Shot(
                    label="positive",
                    role="user:1:vacancy:positive",
                    text="good1",
                    vector=np.ones(16, np.float32),
                ),
            ),
            (
                _pid("u1-2"),
                Shot(
                    label="negative",
                    role="user:1:vacancy:negative",
                    text="bad1",
                    vector=np.full(16, 0.5, np.float32),
                ),
            ),
        ]
    )
    # User 2: 1 shot.
    wired_store.upsert_shots_with_ids(
        [
            (
                _pid("u2-1"),
                Shot(
                    label="positive",
                    role="user:2:vacancy:positive",
                    text="good2",
                    vector=np.full(16, 2.0, np.float32),
                ),
            ),
        ]
    )

    wired_store.delete_by_role_prefix("user:1")

    pos, neg, _, _ = wired_store.load()
    # User 2's positive is still present.
    assert pos.shape == (1, 16)
    assert neg.shape == (0, 16)
    expected = np.full(16, 2.0, np.float32)
    expected = expected / np.linalg.norm(expected)
    np.testing.assert_allclose(pos[0], expected, atol=1e-5)


# ---------------------------------------------------------------------------
# ensure_collection: dim mismatch = recreate
# ---------------------------------------------------------------------------


def _collection_dim(qdrant: QdrantClient, name: str) -> int:
    """Read the dim of a Qdrant collection regardless of whether
    the response uses bare ``VectorParams`` or a
    ``Dict[str, VectorParams]`` (Qdrant server version variance).
    """
    from job_ftch.infrastructure.relevance.shot_anchor import (
        _extract_vector_dim,
    )

    info = qdrant.get_collection(name)
    dim = _extract_vector_dim(info.config.params.vectors)
    assert dim is not None, f"collection {name!r} has no dim"
    return dim


def _make_store(qdrant: QdrantClient, *, dim: int, collection: str) -> BgeMThreeQdrantShotStore:
    """Build a store backed by the in-memory Qdrant client.

    Bypasses the URL constructor (which rejects the
    ``:memory:`` scheme) by setting ``_client`` directly.
    """
    store = BgeMThreeQdrantShotStore.__new__(BgeMThreeQdrantShotStore)
    store._client = qdrant
    store._collection = collection
    store._provider = _StubProvider(dim=dim)
    store._max_length = 1024
    return store


def test_ensure_collection_creates_with_provider_dim(
    qdrant: QdrantClient,
) -> None:
    store = _make_store(qdrant, dim=128, collection="col-a")
    store.ensure_collection()
    assert _collection_dim(qdrant, "col-a") == 128


def test_ensure_collection_migrates_on_dim_change(
    qdrant: QdrantClient,
) -> None:
    """On dim mismatch, ensure_collection migrates to a versioned collection
    rather than destroying existing shots. The new collection is preserved.
    """
    # 1) Seed at 128 dim.
    store_128 = _make_store(qdrant, dim=128, collection="col-b")
    store_128.ensure_collection()
    qdrant.upsert(
        collection_name="col-b",
        points=[
            rest.PointStruct(
                id=0,
                vector=np.ones(128, np.float32).tolist(),
                payload={"label": "positive", "role": "x", "text": "t"},
            )
        ],
    )
    assert qdrant.count("col-b").count == 1

    # 2) New model: 16 dim. ensure_collection migrates to col-b_v16.
    store_16 = _make_store(qdrant, dim=16, collection="col-b")
    store_16.ensure_collection()
    # Old collection is preserved (shots not destroyed).
    assert qdrant.count("col-b").count == 1
    assert _collection_dim(qdrant, "col-b") == 128
    # New versioned collection created for 16-dim vectors.
    assert qdrant.collection_exists("col-b_v16")
    assert _collection_dim(qdrant, "col-b_v16") == 16
    assert store_16._collection == "col-b_v16"


def test_ensure_collection_idempotent_when_dim_matches(
    qdrant: QdrantClient,
) -> None:
    """Calling ensure_collection twice with the same dim keeps the
    collection intact (no spurious recreate).
    """
    store = _make_store(qdrant, dim=64, collection="col-c")
    store.ensure_collection()
    qdrant.upsert(
        collection_name="col-c",
        points=[
            rest.PointStruct(
                id=0,
                vector=np.ones(64, np.float32).tolist(),
                payload={"label": "positive", "role": "r", "text": "t"},
            )
        ],
    )
    before = qdrant.count("col-c").count
    store.ensure_collection()  # second call
    after = qdrant.count("col-c").count
    assert before == after == 1


# ---------------------------------------------------------------------------
# Stale-dim records are skipped on load
# ---------------------------------------------------------------------------


def test_load_skips_stale_dim_records(qdrant: QdrantClient) -> None:
    """If a single point in the collection has a different dim from
    the others (e.g. a model swap mid-flight), load() must skip it
    rather than crash the relevance scorer.

    The Qdrant in-memory backend rejects an upsert whose vector
    length does not match the collection's, so we exercise the
    same skip path by direct call to the loader with a
    synthetic mismatched record. The point is that the
    *production* load() function must never raise on a stale-dim
    point in the collection.
    """
    store = _make_store(qdrant, dim=16, collection="col-d")
    store.ensure_collection()
    # The 16-dim point is the only thing in the collection; the
    # dim-skip branch is exercised by the synthetic records
    # below.
    qdrant.upsert(
        collection_name="col-d",
        points=[
            rest.PointStruct(
                id=_pid("ok-16"),
                vector=np.ones(16, np.float32).tolist(),
                payload={"label": "positive", "role": "ok", "text": "ok"},
            ),
        ],
    )
    pos, neg, _, _ = store.load()
    assert pos.shape == (1, 16)

    # Now exercise the stale-dim skip path directly by calling
    # ``load`` on a stub client that returns a mixed-dim
    # response. The function must drop the 512-dim record
    # without raising.
    class _FakeRecord:
        def __init__(self, vec: list[float], label: str, role: str, text: str) -> None:
            self.vector = vec
            self.payload = {"label": label, "role": role, "text": text}

    class _FakeClient:
        def scroll(self, **kw: Any) -> tuple[list, Any]:
            return (
                [
                    _FakeRecord(np.ones(16, np.float32).tolist(), "positive", "ok", "ok"),
                    _FakeRecord(np.ones(512, np.float32).tolist(), "positive", "stale", "stale"),
                ],
                None,
            )

    store._client = _FakeClient()  # type: ignore[attr-defined]
    pos_d, neg_d, _, _ = store.load()
    assert pos_d.shape == (1, 16), "512-dim point must be dropped, not crash"
    assert neg_d.shape == (0, 16)


# ---------------------------------------------------------------------------
# Sparse round-trip
# ---------------------------------------------------------------------------


def test_sparse_weights_round_trip(
    wired_store: BgeMThreeQdrantShotStore,
) -> None:
    """Sparse weights from the bot's encoded shot must be loaded
    back as the same dict so the BGE-M3 scorer's
    score_sparse can use them.
    """
    shot = Shot(
        label="positive",
        role="user:1:vacancy:positive",
        text="LLM Engineer",
        vector=np.ones(16, np.float32),
        sparse_weights={"42": 1.0, "100": 0.5},
    )
    wired_store.upsert_shots_with_ids([(_pid("p-sparse"), shot)])
    pos, neg, pos_sparse, neg_sparse = wired_store.load()
    assert pos_sparse[0] == {"42": 1.0, "100": 0.5}


def test_sparse_margin_is_normalized_and_contrastive() -> None:
    scorer = BgeMThreeShotScorer(
        np.zeros((0, 2), np.float32),
        np.zeros((0, 2), np.float32),
        pos_sparse=[{"1": 1.0, "2": 1.0}],
        neg_sparse=[{"9": 100.0}],
        top_k=1,
    )

    positive, negative = scorer.sparse_components({"1": 1.0, "2": 1.0})

    assert positive == pytest.approx(1.0)
    assert negative == pytest.approx(0.0)
    assert scorer.score_sparse({"1": 1.0, "2": 1.0}) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# End-to-end: build a scorer from a Qdrant store
# ---------------------------------------------------------------------------


def test_qdrant_store_feeds_bgem3_scorer(
    wired_store: BgeMThreeQdrantShotStore,
) -> None:
    """The pipeline contract: ``BgeMThreeQdrantShotStore.load()``
    produces dense + sparse outputs that the BGE-M3 scorer
    accepts directly, and a query that matches a positive
    scores high on the positive side.
    """
    p = Shot(
        label="positive",
        role="user:1:vacancy:positive",
        text="LLM Engineer",
        vector=np.zeros(16, np.float32),
    )
    p.vector[0] = 1.0
    n = Shot(
        label="negative",
        role="user:1:vacancy:negative",
        text="Marketing Intern",
        vector=np.zeros(16, np.float32),
    )
    n.vector[1] = 1.0
    wired_store.upsert_shots_with_ids([(_pid("p1"), p), (_pid("n1"), n)])

    pos, neg, pos_s, neg_s = wired_store.load()
    scorer = BgeMThreeShotScorer(pos, neg, pos_sparse=pos_s, neg_sparse=neg_s)

    # Query vector == positive vector -> sim_pos ~ 1.0.
    q_match = np.zeros(16, np.float32)
    q_match[0] = 1.0
    score_match = scorer.score_vector(q_match)
    assert score_match.sim_pos > 0.5

    # Query vector == negative vector -> sim_pos ~ 0.0.
    q_neg = np.zeros(16, np.float32)
    q_neg[1] = 1.0
    score_neg = scorer.score_vector(q_neg)
    assert score_neg.sim_pos < score_match.sim_pos


def test_load_can_filter_by_tenant_id(
    wired_store: BgeMThreeQdrantShotStore,
) -> None:
    a_pos = Shot(
        label="positive",
        role="user:1@tenant:ai_jobs:vacancy:positive",
        text="AI Engineer",
        vector=np.ones(16, np.float32),
    )
    a_neg = Shot(
        label="negative",
        role="user:1@tenant:ai_jobs:vacancy:negative",
        text="Sales Manager",
        vector=np.full(16, 0.5, np.float32),
    )
    d_pos = Shot(
        label="positive",
        role="user:2@tenant:default:vacancy:positive",
        text="Default Tenant Positive",
        vector=np.full(16, 0.25, np.float32),
    )
    wired_store.upsert_shots_with_ids(
        [
            (_pid("ai-pos"), a_pos),
            (_pid("ai-neg"), a_neg),
            (_pid("default-pos"), d_pos),
        ]
    )

    pos, neg, _, _ = wired_store.load(tenant_id="ai_jobs")

    assert pos.shape == (1, 16)
    assert neg.shape == (1, 16)


def test_qdrant_filter_uses_exact_tenant_id() -> None:
    flt = BgeMThreeQdrantShotStore._build_role_filter(role_prefix=None, tenant_id="acme")

    assert flt is not None
    assert len(flt.must) == 1
    condition = flt.must[0]
    assert condition.key == "tenant_id"
    assert isinstance(condition.match, rest.MatchValue)
    assert condition.match.value == "acme"


def test_qdrant_filter_can_include_user_id() -> None:
    flt = BgeMThreeQdrantShotStore._build_role_filter(
        role_prefix=None,
        tenant_id="acme",
        user_id="u42",
    )

    assert flt is not None
    assert len(flt.must) == 2
    tenant_condition = flt.must[0]
    user_condition = flt.must[1]
    assert tenant_condition.key == "tenant_id"
    assert user_condition.key == "user_id"
    assert isinstance(user_condition.match, rest.MatchValue)
    assert user_condition.match.value == "u42"


def test_qdrant_payload_includes_tenant_id_field(
    wired_store: BgeMThreeQdrantShotStore,
) -> None:
    shot = Shot(
        label="positive",
        role="user:42@tenant:tenant_a:vacancy:positive",
        text="Tenant scoped shot",
        vector=np.ones(16, dtype=np.float32),
    )

    wired_store.upsert_shots_with_ids([(_pid("tenant-payload"), shot)])
    records, _ = wired_store._client.scroll(  # type: ignore[attr-defined]
        collection_name=wired_store._collection,  # type: ignore[attr-defined]
        with_payload=True,
        with_vectors=False,
        limit=10,
    )

    assert len(records) == 1
    payload = records[0].payload or {}
    assert payload["tenant_id"] == "tenant_a"
    assert payload["user_id"] == "42"


def test_load_can_filter_by_role_prefix(
    wired_store: BgeMThreeQdrantShotStore,
) -> None:
    u1 = Shot(
        label="positive",
        role="user:1@tenant:t:vacancy:positive",
        text="User One",
        vector=np.ones(16, np.float32),
    )
    u2 = Shot(
        label="positive",
        role="user:2@tenant:t:vacancy:positive",
        text="User Two",
        vector=np.full(16, 0.25, np.float32),
    )
    wired_store.upsert_shots_with_ids([(_pid("u1"), u1), (_pid("u2"), u2)])

    pos, neg, _, _ = wired_store.load(role_prefix="user:1@tenant:t:")

    assert pos.shape == (1, 16)
    assert neg.shape == (0, 16)


def test_load_can_filter_by_user_id(
    wired_store: BgeMThreeQdrantShotStore,
) -> None:
    u1 = Shot(
        label="positive",
        role="user:1@tenant:t:vacancy:positive",
        text="User One",
        vector=np.ones(16, np.float32),
    )
    u2 = Shot(
        label="positive",
        role="user:2@tenant:t:vacancy:positive",
        text="User Two",
        vector=np.full(16, 0.25, np.float32),
    )
    wired_store.upsert_shots_with_ids([(_pid("u1"), u1), (_pid("u2"), u2)])

    pos, neg, _, _ = wired_store.load(tenant_id="t", user_id="1")

    assert pos.shape == (1, 16)
    assert neg.shape == (0, 16)


# ---------------------------------------------------------------------------
# clear() is a test-reset helper
# ---------------------------------------------------------------------------


def test_clear_drops_every_point(wired_store: BgeMThreeQdrantShotStore) -> None:
    s = Shot(label="positive", role="r", text="t", vector=np.ones(16, np.float32))
    wired_store.upsert_shots_with_ids([(_pid("p"), s)])
    assert wired_store.load()[0].shape == (1, 16)
    wired_store.clear()
    pos, _, _, _ = wired_store.load()
    assert pos.shape == (0, 16)
