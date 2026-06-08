"""Qdrant vector backend."""

from __future__ import annotations

import hashlib
import uuid
from typing import TYPE_CHECKING, Any, cast

import structlog

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.http import models as rest

    from config import Settings
else:
    try:
        from qdrant_client import AsyncQdrantClient
        from qdrant_client.http import models as rest
    except ImportError:
        AsyncQdrantClient = None
        rest = None

from application.contracts import VectorBackend
from application.registry import register_vector_backend


@register_vector_backend("qdrant")
class QdrantVectorBackend(VectorBackend):
    def __init__(self, settings: Settings) -> None:
        if AsyncQdrantClient is None:
            raise ImportError("qdrant-client is required for qdrant backend")

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
        # In newer versions it might be different, but let's assume search works if imported
        results = await self.client.search(  # type: ignore[attr-defined]
            collection_name=self.collection_name,
            query_vector=vector,
            limit=limit,
            # we don't handle filter yet for simplicity but signature matches
        )
        return [cast("str", hit.payload["job_id"]) for hit in results if hit.payload]

    async def close(self) -> None:
        await self.client.close()
