"""Profile-query semantic evidence built from the already embedded vacancy."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from job_ftch.domain import JobRecord


class ProfileSemanticEvidenceNode:
    """Compare a job vector with cached profile intent and anti-intent queries.

    Query vectors are made once while the run is built.  This node never
    re-encodes the vacancy and never makes a routing decision.
    """

    def __init__(self, positive_vectors: np.ndarray, negative_vectors: np.ndarray) -> None:
        self._positive = positive_vectors
        self._negative = negative_vectors

    async def process(self, item: JobRecord) -> JobRecord:
        raw = item.metadata.get("bgem3_dense")
        if raw is None:
            return item
        vector = np.asarray(raw, dtype=np.float32)
        positive = float(np.max(self._positive @ vector)) if self._positive.size else 0.0
        negative = float(np.max(self._negative @ vector)) if self._negative.size else 0.0
        margin = positive - negative
        return item.model_copy(
            update={
                "metadata": {
                    **item.metadata,
                    "profile_semantic_positive": round(positive, 4),
                    "profile_semantic_negative": round(negative, 4),
                    "profile_semantic_margin": round(margin, 4),
                }
            }
        )
