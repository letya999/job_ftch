"""Qdrant vector backend."""

from __future__ import annotations

import hashlib
import uuid
from typing import TYPE_CHECKING, Any, cast

import structlog

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

from job_ftch.application.contracts import VectorBackend
from job_ftch.application.registry import register_vector_backend


@register_vector_backend("qdrant")
class QdrantVectorBackend(VectorBackend):
    def __init__(self, settings: Settings) -> None:
        if AsyncQdrantClient is None or rest is None:
            raise ImportError(
                "Qdrant backend requires the 'qdrant' extra: pip install job-ftch[qdrant]"
            ) from _IMPORT_ERROR

        self.url = str(settings.qdrant_url)
        self.api_key = settings.qdrant_api_key
        self.collection_name = settings.qdrant_collection

        self.client = AsyncQdrantClient(
            url=self.url,
            api_key=self.api_key,
        )
        self.logger = structlog.get_logger(__name__).bind(backend="qdrant")

    async def upsert(self, job_id: str, vector: list[float], payload: dict[str, Any]) -> None:
        # We use a stable UUID derived from job_id
        point_id = str(uuid.UUID(hashlib.md5(job_id.encode(), usedforsecurity=False).hexdigest()))

        await self.client.upsert(
            collection_name=self.collection_name,
            points=[
                rest.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={**payload, "job_id": job_id},
                )
            ],
        )

    async def search(
        self, vector: list[float], limit: int, filter: dict[str, Any] | None = None
    ) -> list[str]:
        result = await self.client.query_points(
            collection_name=self.collection_name,
            query=vector,
            limit=limit,
            with_payload=True,
        )
        return [cast("str", hit.payload["job_id"]) for hit in result.points if hit.payload]

    async def close(self) -> None:
        await self.client.close()
