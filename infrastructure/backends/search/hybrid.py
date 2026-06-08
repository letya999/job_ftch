"""Hybrid search backend combining FTS and Vector search."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from application.contracts import (
    EmbeddingProvider,
    JobGroupStore,
    JobPersistenceBackend,
    SearchBackend,
    VectorBackend,
)
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
        # We need a job backend that implements both SearchBackend (FTS) 
        # and JobGroupStore (to load groups)
        self.fts_backend = create_job_backend(settings)
        if not isinstance(self.fts_backend, SearchBackend):
            raise TypeError(f"Backend {type(self.fts_backend)} must implement SearchBackend")
        if not isinstance(self.fts_backend, JobGroupStore):
            raise TypeError(f"Backend {type(self.fts_backend)} must implement JobGroupStore")
        if not isinstance(self.fts_backend, JobPersistenceBackend):
            raise TypeError(f"Backend {type(self.fts_backend)} must implement JobPersistenceBackend")

        self.job_group_store = cast(JobGroupStore, self.fts_backend)
        self.job_backend = cast(JobPersistenceBackend, self.fts_backend)
        self.fts_searcher = cast(SearchBackend, self.fts_backend)
        
        self.embedding_provider = None
        self.vector_backend = None
        
        if settings.vector_backend:
            self.vector_backend = create_vector_backend(settings)
            self.embedding_provider = create_embedding_provider(settings)

    async def search(self, query: str, limit: int = 20) -> list[JobGroup]:
        query = query.strip()
        if not query:
            # Fallback to just returning latest if no query
            return await self.fts_searcher.search(query, limit)

        # 1. Run FTS search
        # We fetch more than limit to have better overlap for RRF
        fts_groups = await self.fts_searcher.search(query, limit * 3)
        fts_group_ids = [g.group_id for g in fts_groups]

        # 2. If vector search is not configured, return FTS results
        if not self.vector_backend or not self.embedding_provider:
            return fts_groups[:limit]

        # 3. Run Vector search
        try:
            if not self.embedding_provider or not self.vector_backend:
                return fts_groups[:limit]

            vectors = await self.embedding_provider.embed([query])
            if not vectors or not vectors[0]:
                return fts_groups[:limit]
                
            vector_job_ids = await self.vector_backend.search(vectors[0], limit * 3)
            
            # Map job_ids to group_ids
            vector_group_ids: list[str] = []
            seen_groups = set()
            for jid in vector_job_ids:
                job = await self.job_backend.get_job(jid)
                if job and "group_id" in job.metadata:
                    gid = str(job.metadata["group_id"])
                    if gid not in seen_groups:
                        seen_groups.add(gid)
                        vector_group_ids.append(gid)
        except Exception:
            # If vector search fails, we fallback to FTS instead of failing the whole request
            # but we might want to log this
            return fts_groups[:limit]

        # 4. Merge results using Reciprocal Rank Fusion
        merged_group_ids = reciprocal_rank_fusion([fts_group_ids, vector_group_ids])

        # 5. Load full JobGroup objects for the top results
        result: list[JobGroup] = []
        for gid in merged_group_ids[:limit]:
            group = await self.job_group_store.get_group(gid)
            if group:
                result.append(group)

        return result
