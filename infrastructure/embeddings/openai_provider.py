"""OpenAI embedding provider."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from openai import AsyncOpenAI

from application.contracts import EmbeddingProvider
from application.registry import register_embedding_provider

if TYPE_CHECKING:
    from config import Settings


@register_embedding_provider("openai")
class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise ValueError("openai_api_key is required for OpenAIEmbeddingProvider")

        self.model = settings.embedding_model or "text-embedding-3-small"
        self._dimensions = settings.embedding_dimensions

        self.client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
        )
        self._logger = structlog.get_logger("job_ftch.openai_embedding")

    @property
    def dimensions(self) -> int:
        if self._dimensions is not None:
            return self._dimensions
        # Default dimensions for some models if not provided
        if "small" in self.model:
            return 1536
        if "large" in self.model:
            return 3072
        return 1536

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        try:
            if self._dimensions is not None:
                response = await self.client.embeddings.create(
                    input=texts, model=self.model, dimensions=self._dimensions
                )
            else:
                response = await self.client.embeddings.create(input=texts, model=self.model)
            return [data.embedding for data in response.data]
        except Exception as e:
            self._logger.error("openai_embedding_failed", error=str(e))
            raise
