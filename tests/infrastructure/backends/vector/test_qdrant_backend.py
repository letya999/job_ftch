"""Offline contract tests for the Qdrant vector backend."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from job_ftch.infrastructure.backends.vector import qdrant


class _Point:
    def __init__(self, *, id: str, vector: list[float], payload: dict[str, Any]) -> None:
        self.id = id
        self.vector = vector
        self.payload = payload


class _VectorParams:
    def __init__(self, *, size: int, distance: str) -> None:
        self.size = size
        self.distance = distance


class _Filter:
    pass


class _FilterSelector:
    def __init__(self, *, filter: _Filter) -> None:
        self.filter = filter


class _Client:
    def __init__(self, *, exists: bool = False, existing_dim: int | None = None) -> None:
        self.exists = exists
        self.existing_dim = existing_dim
        self.created: list[tuple[str, _VectorParams]] = []
        self.deleted_collections: list[str] = []
        self.upserts: list[tuple[str, list[_Point]]] = []
        self.delete_calls: list[tuple[str, _FilterSelector]] = []
        self.closed = False

    async def collection_exists(self, _: str) -> bool:
        return self.exists

    async def create_collection(
        self, *, collection_name: str, vectors_config: _VectorParams
    ) -> None:
        self.created.append((collection_name, vectors_config))

    async def get_collection(self, _: str) -> object:
        vectors = {"default": SimpleNamespace(size=self.existing_dim)}
        return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors=vectors)))

    async def delete_collection(self, name: str) -> None:
        self.deleted_collections.append(name)

    async def upsert(self, *, collection_name: str, points: list[_Point]) -> None:
        self.upserts.append((collection_name, points))

    async def query_points(self, **_: object) -> object:
        return SimpleNamespace(
            points=[SimpleNamespace(payload={"job_id": "job-1"}), SimpleNamespace(payload=None)]
        )

    async def count(self, *, collection_name: str) -> object:
        return SimpleNamespace(count=3)

    async def delete(self, *, collection_name: str, points_selector: _FilterSelector) -> None:
        self.delete_calls.append((collection_name, points_selector))

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> tuple[qdrant.QdrantVectorBackend, _Client]:
    client = _Client()
    monkeypatch.setattr(
        qdrant,
        "rest",
        SimpleNamespace(
            PointStruct=_Point,
            VectorParams=_VectorParams,
            Distance=SimpleNamespace(COSINE="cosine"),
            Filter=_Filter,
            FilterSelector=_FilterSelector,
        ),
    )
    instance = qdrant.QdrantVectorBackend.__new__(qdrant.QdrantVectorBackend)
    instance.client = client
    instance.collection_name = "jobs"
    instance.expected_dim = 3
    instance.logger = SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
    )
    return instance, client


def test_extract_vector_dim_supports_single_and_named_configs() -> None:
    assert qdrant._extract_vector_dim(None) is None
    assert qdrant._extract_vector_dim({}) is None
    assert qdrant._extract_vector_dim(SimpleNamespace(size=1024)) == 1024
    assert qdrant._extract_vector_dim({"dense": SimpleNamespace(size=768)}) == 768


@pytest.mark.asyncio
async def test_ensure_collection_creates_or_recreates_only_for_stale_dimension(backend) -> None:
    instance, client = backend

    await instance.ensure_collection(dim=3)
    assert client.created[0][1].size == 3
    with pytest.raises(ValueError, match="positive"):
        await instance.ensure_collection(dim=0)

    client.exists = True
    client.existing_dim = 2
    await instance.ensure_collection(dim=3)
    assert client.deleted_collections == ["jobs"]
    assert len(client.created) == 2

    client.existing_dim = 3
    await instance.ensure_collection(dim=3)
    assert len(client.created) == 2


@pytest.mark.asyncio
async def test_qdrant_backend_validates_vectors_and_maps_payloads(backend) -> None:
    instance, client = backend

    await instance.upsert_many([("job-1", [0.1, 0.2, 0.3], {"tenant": "acme"})])
    point = client.upserts[0][1][0]
    assert point.payload == {"tenant": "acme", "job_id": "job-1"}
    assert point.id == "ac15a52e-59f3-63d6-5344-2a598ffab484"
    assert await instance.search([0.1, 0.2, 0.3], limit=5) == ["job-1"]
    with pytest.raises(ValueError, match="vector dim 2"):
        await instance.upsert_many([("bad", [0.1, 0.2], {})])
    with pytest.raises(ValueError, match="search vector dim 2"):
        await instance.search([0.1, 0.2], limit=1)
    await instance.upsert_many([])
    assert len(client.upserts) == 1


@pytest.mark.asyncio
async def test_qdrant_backend_clear_and_close_preserve_collection(backend) -> None:
    instance, client = backend

    assert await instance.clear() == 3
    assert client.delete_calls[0][0] == "jobs"
    assert isinstance(client.delete_calls[0][1].filter, _Filter)
    await instance.close()
    assert client.closed is True
