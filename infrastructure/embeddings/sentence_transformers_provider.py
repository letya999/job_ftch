"""SentenceTransformers embedding provider."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = cast("Any", None)

from application.contracts import EmbeddingProvider
from application.registry import register_embedding_provider

if TYPE_CHECKING:
    from config import Settings


@register_embedding_provider("sentence_transformers")
class SentenceTransformersProvider(EmbeddingProvider):
    def __init__(self, settings: Settings) -> None:
        if SentenceTransformer is None:
            raise ImportError(
                "sentence-transformers is not installed. Install it with: pip install sentence-transformers torch"
            )

        self.model_name = settings.embedding_model
        # Load model synchronously during initialization. In production, this might be slow,
        # but it's loaded only once.
        self._model = SentenceTransformer(self.model_name)
        self._dimensions = (
            settings.embedding_dimensions or self._model.get_sentence_embedding_dimension()
        )

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        # Run inference in thread pool to avoid blocking the event loop
        loop = asyncio.get_running_loop()
        embeddings = await loop.run_in_executor(None, self._model.encode, texts)

        # Convert numpy arrays to lists of floats
        return [emb.tolist() for emb in embeddings]
