"""Qdrant vector backend."""

from __future__ import annotations

import hashlib
import uuid
from typing import TYPE_CHECKING, Any, cast

import structlog

try:
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.http import models as rest
except ImportError:
    AsyncQdrantClient = cast("Any", None)  # type: ignore[misc]
    rest = cast("Any", None)

from application.contracts import VectorBackend
from application.registry import register_vector_backend

if TYPE_CHECKING:
    from config import Settings


@register_vector_backend("qdrant")
class QdrantBackend(VectorBackend):
    def __init__(self, settings: Settings) -> None:
        if AsyncQdrantClient is None:
            raise ImportError("qdrant-client is required for qdrant backend")
        if not settings.qdrant_url:
            raise ValueError("qdrant_url is required for QdrantBackend")

        self.collection_name = settings.qdrant_collection
        self._dimensions = settings.embedding_dimensions

        self.client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
        )
        self._logger = structlog.get_logger("job_ftch.qdrant_backend")
        self._collection_ensured = False

    async def _ensure_collection(self, dimensions: int) -> None:
        if self._collection_ensured:
            return

        try:
            # Check if exists
            await self.client.get_collection(self.collection_name)
        except Exception:
            # Try to create
            try:
                await self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=rest.VectorParams(
                        size=dimensions,
                        distance=rest.Distance.COSINE,
                    ),
                )
            except Exception as e:
                # Might have been created concurrently
                self._logger.warning("qdrant_create_collection_failed", error=str(e))

        self._collection_ensured = True
        self._dimensions = dimensions

    def _job_id_to_uuid(self, job_id: str) -> str:
        # Qdrant requires UUID or integer for point IDs.
        # Deterministic UUID from stable job_id.
        # usedforsecurity=False for python 3.9+ compatibility and intent
        m = hashlib.md5(job_id.encode("utf-8"), usedforsecurity=False)
        return str(uuid.UUID(bytes=m.digest()))

    async def upsert(
        self,
        job_id: str,
        vector: list[float],
        payload: dict[str, object],
    ) -> None:
        dim = len(vector)
        if self._dimensions is None:
            await self._ensure_collection(dim)
        elif self._dimensions != dim:
            raise ValueError(f"Vector dimension mismatch. Expected {self._dimensions}, got {dim}")
        elif not self._collection_ensured:
            await self._ensure_collection(self._dimensions)

        point_id = self._job_id_to_uuid(job_id)

        # Store original job_id in payload if not present
        if "job_id" not in payload:
            payload["job_id"] = job_id

        await self.client.upsert(
            collection_name=self.collection_name,
            points=[
                rest.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=payload,
                )
            ],
        )

    async def search(
        self,
        vector: list[float],
        limit: int,
        filter: dict[str, object] | None = None,
    ) -> list[str]:
        if not self._collection_ensured:
            if self._dimensions:
                await self._ensure_collection(self._dimensions)
            else:
                await self._ensure_collection(len(vector))

        dim = len(vector)
        if self._dimensions and self._dimensions != dim:
            raise ValueError(
                f"Query vector dimension mismatch. Expected {self._dimensions}, got {dim}"
            )

        query_filter = None
        if filter:
            conditions: list[Any] = []
            for k, v in filter.items():
                conditions.append(
                    rest.FieldCondition(key=k, match=rest.MatchValue(value=cast("Any", v)))
                )
            query_filter = rest.Filter(must=conditions)

        results = await self.client.query_points(
            collection_name=self.collection_name,
            query=vector,
            limit=limit,
            query_filter=query_filter,
            with_payload=["job_id"],
        )

        job_ids = []
        for scored_point in results.points:
            if scored_point.payload and "job_id" in scored_point.payload:
                job_ids.append(str(scored_point.payload["job_id"]))

        return job_ids
