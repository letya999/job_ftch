"""Qdrant vector backend."""

from __future__ import annotations

import hashlib
import uuid
from typing import TYPE_CHECKING, Any, cast

import structlog

_IMPORT_ERROR: Exception | None = None

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.http import models as rest

    from job_ftch.config import Settings
else:
    try:
        from qdrant_client import AsyncQdrantClient
        from qdrant_client.http import models as rest

        _IMPORT_ERROR = None
    except ImportError as exc:
        AsyncQdrantClient = None
        rest = None
        _IMPORT_ERROR = exc

from job_ftch.application.contracts import VectorBackend  # noqa: E402
from job_ftch.application.registry import register_vector_backend  # noqa: E402


def _extract_vector_dim(vectors_config: Any) -> int | None:
    """Read the dim out of a Qdrant ``vectors_config`` regardless of
    whether it is a single ``VectorParams`` or a name->params map.
    """
    if vectors_config is None:
        return None
    if isinstance(vectors_config, dict):
        if not vectors_config:
            return None
        first = next(iter(vectors_config.values()))
        return getattr(first, "size", None)
    return getattr(vectors_config, "size", None)


@register_vector_backend("qdrant")
class QdrantVectorBackend(VectorBackend):
    def __init__(self, settings: Settings) -> None:
        if AsyncQdrantClient is None or rest is None:
            raise ImportError(
                "Qdrant backend requires the 'qdrant' extra: pip install job-ftch[qdrant]"
            ) from _IMPORT_ERROR

        self.url = str(settings.qdrant_url)
        self.api_key = (
            settings.qdrant_api_key.get_secret_value()
            if settings.qdrant_api_key is not None
            else None
        )
        self.collection_name = settings.qdrant_collection
        self.expected_dim = settings.embedding_dimensions

        self.client = AsyncQdrantClient(
            url=self.url,
            api_key=self.api_key,
        )
        self.logger = structlog.get_logger(__name__).bind(backend="qdrant")

    async def ensure_collection(self, *, dim: int) -> None:
        """Create the Qdrant collection with the right dim, or recreate
        it if the dim is stale (e.g. someone switched the embedding
        model and never re-created the collection). The bot
        previously crashed with
        ``Wrong input: Vector dimension error: expected dim: 1536,
        got 1024`` because the Qdrant collection was created with
        OpenAI's 1536-dim text-embedding-3-small but the BGE-M3
        pipeline started writing 1024-dim vectors into the same
        collection. This method prevents the next occurrence.
        """
        if dim <= 0:
            raise ValueError(f"ensure_collection: dim must be positive, got {dim}")
        if not await self.client.collection_exists(self.collection_name):
            await self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=rest.VectorParams(size=dim, distance=rest.Distance.COSINE),
            )
            self.logger.info(
                "qdrant_collection_created",
                collection=self.collection_name,
                dim=dim,
            )
            return
        info = await self.client.get_collection(self.collection_name)
        existing = (
            _extract_vector_dim(info.config.params.vectors)
            if info and info.config and info.config.params
            else None
        )
        if existing != dim:
            self.logger.warning(
                "qdrant_collection_dim_mismatch_recreate",
                collection=self.collection_name,
                existing=existing,
                expected=dim,
            )
            await self.client.delete_collection(self.collection_name)
            await self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=rest.VectorParams(size=dim, distance=rest.Distance.COSINE),
            )

    async def upsert(self, job_id: str, vector: list[float], payload: dict[str, Any]) -> None:
        await self.upsert_many([(job_id, vector, payload)])

    async def upsert_many(
        self,
        records: list[tuple[str, list[float], dict[str, Any]]],
    ) -> None:
        if not records:
            return
        for _job_id, vector, _payload in records:
            if self.expected_dim is not None and len(vector) != self.expected_dim:
                raise ValueError(
                    f"vector dim {len(vector)} does not match expected "
                    f"dim {self.expected_dim} for collection "
                    f"{self.collection_name}. The collection was created "
                    f"for a different embedding model; ensure_collection "
                    f"before upserting."
                )
        points: list[rest.PointStruct] = []
        for job_id, vector, payload in records:
            point_id = str(
                uuid.UUID(hashlib.md5(job_id.encode(), usedforsecurity=False).hexdigest())
            )
            points.append(
                rest.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={**payload, "job_id": job_id},
                )
            )

        await self.client.upsert(
            collection_name=self.collection_name,
            points=points,
        )

    async def search(
        self, vector: list[float], limit: int, filter: dict[str, Any] | None = None
    ) -> list[str]:
        if self.expected_dim is not None and len(vector) != self.expected_dim:
            raise ValueError(
                f"search vector dim {len(vector)} does not match collection dim {self.expected_dim}"
            )
        result = await self.client.query_points(
            collection_name=self.collection_name,
            query=vector,
            limit=limit,
            with_payload=True,
        )
        return [cast("str", hit.payload["job_id"]) for hit in result.points if hit.payload]

    async def clear(self) -> int:
        try:
            count_result = await self.client.count(collection_name=self.collection_name)
            total = int(getattr(count_result, "count", 0) or 0)
        except Exception:
            total = 0
        # Delete every point but preserve the collection schema.
        await self.client.delete(
            collection_name=self.collection_name,
            points_selector=rest.FilterSelector(filter=rest.Filter()),
        )
        self.logger.info("qdrant_cleared", collection=self.collection_name, deleted=total)
        return total

    async def close(self) -> None:
        await self.client.close()
