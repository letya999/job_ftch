"""Tests for E5 query/passage prefix methods on FastEmbedProvider."""

from __future__ import annotations

import pytest


class _MockFastEmbedModel:
    """Mock that captures the texts passed to embed()."""

    def __init__(self) -> None:
        self.last_texts: list[str] = []

    def embed(self, texts: list[str]):
        self.last_texts = list(texts)
        import numpy as np

        return [np.array([0.1, 0.2, 0.3]) for _ in texts]


@pytest.mark.asyncio
async def test_embed_query_adds_prefix(monkeypatch):
    from job_ftch.infrastructure.llm.fastembed_provider import FastEmbedProvider

    provider = FastEmbedProvider()
    mock_model = _MockFastEmbedModel()
    provider._model = mock_model

    result = await provider.embed_query(["Python developer"])
    assert mock_model.last_texts == ["query: Python developer"]
    assert len(result) == 1


@pytest.mark.asyncio
async def test_embed_passage_adds_prefix(monkeypatch):
    from job_ftch.infrastructure.llm.fastembed_provider import FastEmbedProvider

    provider = FastEmbedProvider()
    mock_model = _MockFastEmbedModel()
    provider._model = mock_model

    result = await provider.embed_passage(["Senior engineer job posting"])
    assert mock_model.last_texts == ["passage: Senior engineer job posting"]
    assert len(result) == 1


@pytest.mark.asyncio
async def test_embed_returns_empty_for_empty_input():
    from job_ftch.infrastructure.llm.fastembed_provider import FastEmbedProvider

    provider = FastEmbedProvider()
    result = await provider.embed_query([])
    assert result == []

    result = await provider.embed_passage([])
    assert result == []
