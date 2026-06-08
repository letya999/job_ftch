"""Ollama embedding provider."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import structlog

from job_ftch.application.contracts import EmbeddingProvider
from job_ftch.application.registry import register_embedding_provider

if TYPE_CHECKING:
    from job_ftch.config import Settings


@register_embedding_provider("ollama")
class OllamaEmbeddingProvider(EmbeddingProvider):
    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.model = settings.embedding_model
        # Ollama doesn't explicitly expose dimensions without pulling or running,
        # but we can configure it or default to a common size like 4096 (llama3) or 768 (nomic)
        self._dimensions = settings.embedding_dimensions or 768
        self._logger = structlog.get_logger("job_ftch.ollama_embedding")

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        embeddings = []
        # Ollama's /api/embeddings endpoint only takes a single prompt.
        # We need to process sequentially or concurrently.
        # For simplicity and not overwhelming local ollama, we do it sequentially.

        async with httpx.AsyncClient() as client:
            for text in texts:
                try:
                    response = await client.post(
                        f"{self.base_url}/api/embeddings",
                        json={
                            "model": self.model,
                            "prompt": text,
                        },
                        timeout=30.0,
                    )
                    response.raise_for_status()
                    data = response.json()
                    embeddings.append(data["embedding"])
                except Exception as e:
                    self._logger.error("ollama_embedding_failed", error=str(e), model=self.model)
                    raise

        return embeddings
