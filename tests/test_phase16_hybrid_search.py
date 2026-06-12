from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from job_ftch.application.registry import register_embedding_provider, register_vector_backend
from job_ftch.config import Settings
from job_ftch.domain import JobRecord, SourceKind
from job_ftch.infrastructure.backends.search.hybrid import HybridSearchBackend

if TYPE_CHECKING:
    from pathlib import Path


class FakeVectorBackend:
    def __init__(self, job_v_id: str) -> None:
        self.job_v_id = job_v_id

    async def search(
        self,
        vector: list[float],
        limit: int,
        filter: dict[str, Any] | None = None,
    ) -> list[str]:
        return [self.job_v_id]

    async def upsert(
        self,
        job_id: str,
        vector: list[float],
        payload: dict[str, Any],
    ) -> None:
        pass


class FakeEmbeddingProvider:
    @property
    def dimensions(self) -> int:
        return 3

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3]]


@pytest.fixture
def hybrid_settings(tmp_path: Path) -> Settings:
    # Setup job ids
    job_vec = JobRecord(
        raw_item_id="v1",
        source_kind=SourceKind.DEBUG,
        source_name="debug",
        title="Vector match",
        description="something else",
        company="Acme",
    )

    # Register fakes
    @register_vector_backend("fake")
    def _fake_v(s: Settings) -> FakeVectorBackend:
        return FakeVectorBackend(job_vec.stable_id)

    @register_embedding_provider("fake")
    def _fake_e(s: Settings) -> FakeEmbeddingProvider:
        return FakeEmbeddingProvider()

    return Settings(
        job_backend="sqlite",
        search_backend="hybrid",
        vector_backend="fake",
        embedding_provider="fake",
        embedding_enabled=True,
        job_store_path=tmp_path / "hybrid.db",
    )


@pytest.mark.asyncio
async def test_hybrid_search_flow(hybrid_settings: Settings) -> None:
    backend = HybridSearchBackend(hybrid_settings)

    # 1. Setup jobs in SQLite (FTS)
    job_fts = JobRecord(
        raw_item_id="f1",
        source_kind=SourceKind.DEBUG,
        source_name="debug",
        title="FTS match",
        description="keyword",
        company="Acme",
    )
    await backend.fts_backend.save(job_fts)

    # 2. Setup job for vector match (we'll manually inject it into SQLite so hybrid can load it)
    job_vec = JobRecord(
        raw_item_id="v1",
        source_kind=SourceKind.DEBUG,
        source_name="debug",
        title="Vector match",
        description="something else",
        company="Acme",
    )
    await backend.fts_backend.save(job_vec)

    # 3. Search
    # Our FTS will match "keyword" -> "f1"
    # Our FakeVector will always return -> stable_id of "v1"
    results = await backend.search("keyword", limit=10)

    assert len(results) >= 1
    # Both should be present in results due to RRF
    ids = [g.canonical_job.raw_item_id for g in results]
    assert "f1" in ids
    assert "v1" in ids

    await backend.fts_backend.close()
