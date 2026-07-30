from __future__ import annotations

import pytest

from job_ftch.nodes.reranker import RerankerNode


class _Reranker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.calls.append((query, documents))
        return [0.8 if "Backend" in query else 0.4]


@pytest.mark.anyio
async def test_reranker_scores_each_profile_against_same_candidate(make_job_record) -> None:
    provider = _Reranker()
    job = make_job_record(title="Python Engineer", description="Build APIs")

    result = await RerankerNode(
        provider, {"backend": "Backend Engineer", "data": "Data Analyst"}
    ).process(job)

    assert provider.calls == [
        ("Backend Engineer", ["Python Engineer\nBuild APIs"]),
        ("Data Analyst", ["Python Engineer\nBuild APIs"]),
    ]
    assert result.metadata["bge_reranker_max_score"] == 0.8
    assert result.metadata["reranker_scores_by_profile"] == {"backend": 0.8, "data": 0.4}


@pytest.mark.anyio
async def test_reranker_failure_is_explicit_degradation(make_job_record) -> None:
    class Failing:
        async def rerank(self, _query: str, _documents: list[str]) -> list[float]:
            raise RuntimeError("offline")

    result = await RerankerNode(Failing(), {"backend": "Backend Engineer"}).process(
        make_job_record()
    )

    assert result.metadata["reranker_degradation"] == "provider_failed"
