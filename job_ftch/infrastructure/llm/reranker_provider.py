"""Cross-encoder reranker using fastembed TextCrossEncoder."""
from __future__ import annotations

import asyncio
from typing import Any

import structlog

from job_ftch.application.registry import register_reranker

logger = structlog.get_logger(__name__)

_DEFAULT_MODEL = "jinaai/jina-reranker-v2-base-multilingual"


@register_reranker("jina-v2-multilingual")
def _create_jina_reranker(settings: object) -> JinaRerankerProvider:
    model_name = getattr(settings, "reranker_model", None) or _DEFAULT_MODEL
    return JinaRerankerProvider(model_name=model_name)


class JinaRerankerProvider:
    """CrossEncoderPort backed by fastembed jina-reranker-v2-base-multilingual.

    Supports 100+ languages including RU/EN/KZ.
    Runs in thread executor to avoid blocking the event loop.
    """

    def __init__(self, model_name: str = _DEFAULT_MODEL) -> None:
        self._model_name = model_name
        self._model: Any = None

    def _get_model(self) -> Any:
        if self._model is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
            self._model = TextCrossEncoder(model_name=self._model_name)
            logger.info("reranker_model_loaded", model=self._model_name)
        return self._model

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Score each document against query. Returns float scores (higher = more relevant)."""
        if not documents:
            return []

        def _sync_rerank() -> list[float]:
            model = self._get_model()
            scores = list(model.rerank(query, documents))
            # fastembed rerank returns objects with .score or floats
            result = []
            for s in scores:
                if hasattr(s, "score"):
                    result.append(float(s.score))
                else:
                    result.append(float(s))
            return result

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _sync_rerank)
