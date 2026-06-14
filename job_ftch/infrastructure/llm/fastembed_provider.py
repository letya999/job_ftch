"""FastEmbed-based local embedding provider (ONNX, no GPU required)."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from job_ftch.application.registry import register_embedding_provider

logger = structlog.get_logger(__name__)

_DEFAULT_MODEL = "intfloat/multilingual-e5-small"  # 384 dims, RU/EN/KZ


@register_embedding_provider("fastembed")
def _create_fastembed_provider(settings: object) -> FastEmbedProvider:
    model_name = getattr(settings, "embedding_model", None) or _DEFAULT_MODEL
    return FastEmbedProvider(model_name=model_name)


class FastEmbedProvider:
    """EmbeddingProvider backed by fastembed ONNX models (local, no API key)."""

    def __init__(self, model_name: str = _DEFAULT_MODEL) -> None:
        self._model_name = model_name
        self._model: object | None = None

    def _get_model(self) -> Any:
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self._model_name)
            logger.info("fastembed_model_loaded", model=self._model_name)
        return self._model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed list of texts. Runs sync fastembed in thread executor."""
        if not texts:
            return []
        loop = asyncio.get_running_loop()

        def _sync_embed() -> list[list[float]]:
            model = self._get_model()
            return [vec.tolist() for vec in model.embed(texts)]

        return await loop.run_in_executor(None, _sync_embed)
