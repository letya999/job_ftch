"""Pipeline node for job vector embeddings."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from job_ftch.application.contracts import ProcessingNode
from job_ftch.application.search_text import build_job_embedding_text
from job_ftch.domain import JobRecord

if TYPE_CHECKING:
    from job_ftch.application.contracts import EmbeddingProvider, VectorBackend


class EmbeddingNode(ProcessingNode[JobRecord]):
    def __init__(
        self,
        provider: EmbeddingProvider,
        vector_backend: VectorBackend,
        *,
        upsert_batch_size: int = 32,
    ) -> None:
        self.provider = provider
        self.vector_backend = vector_backend
        self._upsert_batch_size = upsert_batch_size
        self._logger = structlog.get_logger("job_ftch.embedding_node")
        self._collection_ready = False
        self._pending_upserts: list[tuple[str, list[float], dict[str, object]]] = []
        self._lock = asyncio.Lock()

    async def process(self, job: JobRecord) -> JobRecord:
        group_id = job.group_id or job.metadata.get("group_id")
        if not group_id:
            raise ValueError("group_id is required in job.metadata for EmbeddingNode")

        text = build_job_embedding_text(job)
        if not text:
            return job

        try:
            embed_fn = getattr(self.provider, "embed_passage", self.provider.embed)
            vectors = await embed_fn([text])
            if vectors and vectors[0]:
                payload: dict[str, object] = {
                    "job_id": job.stable_id,
                    "group_id": str(group_id),
                    "source_kind": str(job.source_kind),
                    "source_name": job.source_name,
                    "company": job.company or "",
                    "company_canonical": job.company_canonical or "",
                    "title": job.title or "",
                    "location": job.location or "",
                    "work_mode": str(job.work_mode) if job.work_mode else "",
                }

                # Lazily create the dedup collection on first write. Without
                # this the collection never exists and every upsert 404s
                # ("Collection job_ftch_jobs doesn't exist"), silently
                # disabling vector dedup and spamming embedding_failed.
                # Guarded by the lock so concurrent item-workers do not issue
                # redundant ensure_collection() calls.
                if not self._collection_ready:
                    async with self._lock:
                        if not self._collection_ready:
                            ensure = getattr(self.vector_backend, "ensure_collection", None)
                            if callable(ensure):
                                await ensure(dim=len(vectors[0]))
                            self._collection_ready = True

                # Single critical section: append the pending upsert and decide
                # whether the batch is full. The flush itself runs outside the
                # lock (asyncio.Lock is not reentrant and _flush re-acquires it).
                async with self._lock:
                    self._pending_upserts.append((job.stable_id, list(vectors[0]), payload))
                    needs_flush = len(self._pending_upserts) >= self._upsert_batch_size
                if needs_flush:
                    await self._flush_pending_upserts()
                # Store vector on job for inline scoring by MultiProfileMatchNode
                updated_metadata = {**job.metadata, "embedding_vector": vectors[0]}
                return job.model_copy(update={"metadata": updated_metadata})
        except Exception as e:
            self._logger.warning("embedding_failed", job_id=job.stable_id, error=str(e))
            # Continue pipeline, vector embeddings are optional infrastructure enhancement

        return job

    async def flush(self) -> None:
        await self._flush_pending_upserts()

    async def _flush_pending_upserts(self) -> None:
        async with self._lock:
            if not self._pending_upserts:
                return
            pending = self._pending_upserts
            self._pending_upserts = []
        try:
            upsert_many = getattr(self.vector_backend, "upsert_many", None)
            if callable(upsert_many):
                await upsert_many(pending)
                return
            for job_id, vector, payload in pending:
                await self.vector_backend.upsert(job_id=job_id, vector=vector, payload=payload)
        except Exception as exc:
            self._logger.warning(
                "embedding_batch_upsert_failed",
                batch_size=len(pending),
                error=str(exc),
            )
