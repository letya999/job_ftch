"""Provider-neutral cross-encoder reranking stage."""

from __future__ import annotations

from time import monotonic
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_ftch.application.contracts import CrossEncoderProvider
    from job_ftch.domain import JobRecord


class RerankerNode:
    """Score a candidate against each target profile without changing recall."""

    produced_metadata = frozenset({"bge_reranker_max_score"})

    def __init__(self, provider: CrossEncoderProvider, profiles: dict[str, str]) -> None:
        self._provider = provider
        self._profiles = dict(profiles)

    async def process(self, item: JobRecord) -> JobRecord:
        if not self._profiles:
            return self._degraded(item, "target_profiles_unavailable")
        document = "\n".join(part for part in (item.title, item.description) if part).strip()
        if not document:
            return self._degraded(item, "candidate_text_unavailable")
        started = monotonic()
        try:
            score_map: dict[str, float] = {}
            for profile_id, policy_text in self._profiles.items():
                scores = await self._provider.rerank(policy_text, [document])
                if len(scores) != 1:
                    return self._degraded(item, "provider_returned_wrong_score_count")
                score_map[profile_id] = float(scores[0])
            return item.model_copy(
                update={
                    "metadata": {
                        **item.metadata,
                        "bge_reranker_max_score": max(score_map.values()),
                        "reranker_scores_by_profile": score_map,
                        "reranker_model": getattr(
                            self._provider, "_model_name", type(self._provider).__name__
                        ),
                        "reranker_latency_ms": round((monotonic() - started) * 1000, 3),
                    }
                }
            )
        except Exception:  # provider failure is an explicit routing degradation
            return self._degraded(item, "provider_failed")

    @staticmethod
    def _degraded(item: JobRecord, reason: str) -> JobRecord:
        return item.model_copy(
            update={"metadata": {**item.metadata, "reranker_degradation": reason}}
        )
