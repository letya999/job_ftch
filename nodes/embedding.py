"""Pipeline node for job vector embeddings."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from application.contracts import ProcessingNode
from domain import Job

if TYPE_CHECKING:
    from application.contracts import EmbeddingProvider, VectorBackend


class EmbeddingNode(ProcessingNode[Job]):
    def __init__(
        self,
        provider: EmbeddingProvider,
        vector_backend: VectorBackend,
    ) -> None:
        self.provider = provider
        self.vector_backend = vector_backend
        self._logger = structlog.get_logger("job_ftch.embedding_node")

    async def process(self, job: Job) -> Job:
        group_id = job.metadata.get("group_id")
        if not group_id:
            raise ValueError("group_id is required in job.metadata for EmbeddingNode")

        from infrastructure.embeddings.text import build_job_embedding_text
        text = build_job_embedding_text(job)
        if not text:
            return job

        try:
            vectors = await self.provider.embed([text])
            if vectors and vectors[0]:
                payload: dict[str, object] = {
                    "job_id": job.raw_item_id,
                    "group_id": str(group_id),
                    "source_kind": str(job.source_kind),
                    "source_name": job.source_name,
                    "company": job.company or "",
                    "company_canonical": job.company_canonical or "",
                    "title": job.title or "",
                    "location": job.location or "",
                    "work_mode": str(job.work_mode) if job.work_mode else "",
                }
                
                await self.vector_backend.upsert(
                    job_id=job.raw_item_id,
                    vector=vectors[0],
                    payload=payload,
                )
        except Exception as e:
            self._logger.warning("embedding_failed", job_id=job.raw_item_id, error=str(e))
            # Continue pipeline, vector embeddings are optional infrastructure enhancement
            
        return job
