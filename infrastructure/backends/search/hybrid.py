"""Hybrid search backend combining FTS and Vector search."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from application.contracts import EmbeddingProvider, JobGroupStore, SearchBackend, VectorBackend
from application.registry import (
    create_embedding_provider,
    create_job_backend,
    create_vector_backend,
    register_search_backend,
)

from .rrf import reciprocal_rank_fusion

if TYPE_CHECKING:
    from config import Settings
    from domain import JobGroup


@register_search_backend("hybrid")
class HybridSearchBackend(SearchBackend):
    def __init__(self, settings: Settings) -> None:
        self.fts_backend = create_job_backend(settings)
        self.job_group_store = cast("JobGroupStore", self.fts_backend)
        
        self.embedding_provider = None
        self.vector_backend = None
        
        if settings.vector_backend:
            self.vector_backend = create_vector_backend(settings)
            self.embedding_provider = create_embedding_provider(settings)

    async def search(self, query: str, limit: int = 20) -> list[JobGroup]:
        query = query.strip()
        if not query:
            # Fallback to just returning latest if no query
            return await cast("SearchBackend", self.fts_backend).search(query, limit)

        # 1. run fulltext backend search
        fts_groups = await cast("SearchBackend", self.fts_backend).search(query, limit * 2)
        fts_group_ids = [g.group_id for g in fts_groups]

        if not self.vector_backend or not self.embedding_provider:
            return fts_groups[:limit]

        # 2. run vector search if configured
        provider = cast("EmbeddingProvider", self.embedding_provider)
        vector_backend = cast("VectorBackend", self.vector_backend)
        vectors = await provider.embed([query])
        if not vectors or not vectors[0]:
            return fts_groups[:limit]
            
        vector_search_result_job_ids = await vector_backend.search(vectors[0], limit * 2)

        vector_group_ids = []
        job_backend = cast("Any", self.fts_backend)
        for jid in vector_search_result_job_ids:
            job = await job_backend.get_job(jid)
            if job and "group_id" in job.metadata:
                vector_group_ids.append(job.metadata["group_id"])

        # Deduplicate preserving order
        seen = set()
        deduped_vector_group_ids = []
        for gid in vector_group_ids:
            if gid not in seen:
                seen.add(gid)
                deduped_vector_group_ids.append(gid)

        # 4. merge with RRF
        merged_group_ids = reciprocal_rank_fusion([fts_group_ids, deduped_vector_group_ids])

        # 5. load JobGroup objects through job backend
        result = []
        for gid in merged_group_ids[:limit]:
            group = await self.job_group_store.get_group(gid)
            if group:
                result.append(group)

        # 6. return list[JobGroup]
        return result
