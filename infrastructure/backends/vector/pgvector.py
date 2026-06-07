"""PgVector backend for job embeddings."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import structlog

try:
    import asyncpg
except ImportError:
    asyncpg = None

from application.contracts import VectorBackend
from application.registry import register_vector_backend

if TYPE_CHECKING:
    from config import Settings


@register_vector_backend("pgvector")
class PgVectorBackend(VectorBackend):
    def __init__(self, settings: Settings) -> None:
        if asyncpg is None:
            raise ImportError("asyncpg is required for pgvector backend")
        if not settings.store_dsn:
            raise ValueError("store_dsn is required for pgvector backend")
            
        self.dsn = settings.store_dsn
        self.pool_min = settings.store_pool_min
        self.pool_max = settings.store_pool_max
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()
        self._logger = structlog.get_logger("job_ftch.pgvector_backend")
        self._dimensions = settings.embedding_dimensions
        self._schema_initialized = False

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            async with self._lock:
                if self._pool is None:
                    self._pool = await asyncpg.create_pool(
                        dsn=self.dsn,
                        min_size=self.pool_min,
                        max_size=self.pool_max,
                    )
        return self._pool

    async def _init_schema(self, dimensions: int) -> None:
        if self._schema_initialized:
            return
            
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            
            # Create table with specific dimensions
            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS jf_job_vectors (
                    job_id TEXT PRIMARY KEY,
                    group_id TEXT NOT NULL,
                    vector VECTOR({dimensions}),
                    payload JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS jf_job_vectors_group_idx
                ON jf_job_vectors (group_id);
            """)
            
        self._schema_initialized = True
        self._dimensions = dimensions

    async def upsert(
        self,
        job_id: str,
        vector: list[float],
        payload: dict[str, object],
    ) -> None:
        dim = len(vector)
        if self._dimensions is None:
            await self._init_schema(dim)
        elif self._dimensions != dim:
            raise ValueError(f"Vector dimension mismatch. Expected {self._dimensions}, got {dim}")
        elif not self._schema_initialized:
            await self._init_schema(self._dimensions)
            
        pool = await self._get_pool()
        
        # Convert list of floats to pgvector string format
        vector_str = "[" + ",".join(str(f) for f in vector) + "]"
        
        group_id = str(payload.get("group_id", ""))
        payload_json = json.dumps(payload)
        
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO jf_job_vectors (job_id, group_id, vector, payload, updated_at)
                VALUES ($1, $2, $3::vector, $4::jsonb, NOW())
                ON CONFLICT (job_id) DO UPDATE SET
                    group_id = EXCLUDED.group_id,
                    vector = EXCLUDED.vector,
                    payload = EXCLUDED.payload,
                    updated_at = EXCLUDED.updated_at
                """,
                job_id, group_id, vector_str, payload_json
            )

    async def search(
        self,
        vector: list[float],
        limit: int,
        filter: dict[str, object] | None = None,
    ) -> list[str]:
        if not self._schema_initialized:
            if self._dimensions:
                await self._init_schema(self._dimensions)
            else:
                await self._init_schema(len(vector))

        pool = await self._get_pool()
        vector_str = "[" + ",".join(str(f) for f in vector) + "]"
        
        # L2 distance operator is <->
        # Cosine distance is <=>
        # Inner product is <#>
        # We use Cosine distance for text embeddings generally
        
        async with pool.acquire() as conn:
            # We skip filter for now since it's optional and pgvector filtering requires JSONB paths.
            # We can implement basic key-value exact match if needed.
            if filter:
                conditions = []
                args = [vector_str, limit]
                idx = 3
                for k, v in filter.items():
                    conditions.append(f"payload->>'{k}' = ${idx}")
                    args.append(str(v))
                    idx += 1
                
                where_clause = " AND ".join(conditions)
                query = f"""
                    SELECT job_id
                    FROM jf_job_vectors
                    WHERE {where_clause}
                    ORDER BY vector <=> $1::vector
                    LIMIT $2
                """
                rows = await conn.fetch(query, *args)
            else:
                query = """
                    SELECT job_id
                    FROM jf_job_vectors
                    ORDER BY vector <=> $1::vector
                    LIMIT $2
                """
                rows = await conn.fetch(query, vector_str, limit)
                
        return [row["job_id"] for row in rows]
