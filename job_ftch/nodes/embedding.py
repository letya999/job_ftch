"""Pipeline node for job vector embeddings."""

from __future__ import annotations

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
    ) -> None:
        self.provider = provider
        self.vector_backend = vector_backend
        self._logger = structlog.get_logger("job_ftch.embedding_node")

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

                await self.vector_backend.upsert(
                    job_id=job.stable_id,
                    vector=vectors[0],
                    payload=payload,
                )
                # Store vector on job for inline scoring by MultiProfileMatchNode
                updated_metadata = {**job.metadata, "embedding_vector": vectors[0]}
                return job.model_copy(update={"metadata": updated_metadata})
        except Exception as e:
            self._logger.warning("embedding_failed", job_id=job.stable_id, error=str(e))
            # Continue pipeline, vector embeddings are optional infrastructure enhancement

        return job
