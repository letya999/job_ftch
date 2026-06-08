"""SentenceTransformers embedding provider."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

    from job_ftch.config import Settings
else:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        SentenceTransformer = None

from job_ftch.application.contracts import EmbeddingProvider
from job_ftch.application.registry import register_embedding_provider


@register_embedding_provider("sentence_transformers")
class SentenceTransformersProvider(EmbeddingProvider):
    def __init__(self, settings: Settings) -> None:
        if SentenceTransformer is None:
            raise ImportError("sentence-transformers is required for this provider")

        self.model_name = settings.embedding_model
        self.model = SentenceTransformer(self.model_name)
        # We wrap it in a lock because SentenceTransformer is not thread-safe
        # and we might be calling it from multiple async tasks
        self._lock = asyncio.Lock()

    @property
    def dimensions(self) -> int:
        # Get dimensions from model
        return int(self.model.get_sentence_embedding_dimension() or 0)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        async with self._lock:
            # Run in a thread pool since sentence-transformers is CPU bound and blocking
            loop = asyncio.get_running_loop()
            embeddings = await loop.run_in_executor(
                None, lambda: self.model.encode(texts, convert_to_numpy=True)
            )

            # Convert numpy array to list of lists
            return cast("list[list[float]]", embeddings.tolist())
