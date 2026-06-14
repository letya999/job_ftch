"""Tests for JinaRerankerProvider (mocked)."""

from __future__ import annotations

import pytest


class _MockCrossEncoder:
    def rerank(self, query: str, documents: list[str]):
        # Return scores: first doc gets highest score
        return [1.0 - (i * 0.1) for i in range(len(documents))]


@pytest.mark.asyncio
async def test_reranker_returns_scores_for_all_docs():
    from job_ftch.infrastructure.llm.reranker_provider import JinaRerankerProvider

    provider = JinaRerankerProvider()
    provider._model = _MockCrossEncoder()

    docs = ["Python developer role", "Java engineer", "DevOps position"]
    scores = await provider.rerank("Python backend engineer", docs)

    assert len(scores) == len(docs)
    assert all(isinstance(s, float) for s in scores)


@pytest.mark.asyncio
async def test_reranker_returns_empty_for_empty_docs():
    from job_ftch.infrastructure.llm.reranker_provider import JinaRerankerProvider

    provider = JinaRerankerProvider()
    result = await provider.rerank("query", [])
    assert result == []
