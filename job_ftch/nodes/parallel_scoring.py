"""Parallel multi-branch scoring: dense margin + sparse margin + role anchor.

Architecture:
  Branch A — dense cosine similarity to positive shot anchors
  Branch B — sparse (lexical) dot-product to positive shot anchors
  Branch C — dense cosine to profile role anchors (target_roles strings)
  Branch D — dense similarity to negative shot anchors (gate)
  Branch E — sparse similarity to negative shot anchors (gate)

Fusion: sigmoid(k * margin) where margin combines dense contrast (A-D),
sparse contrast (B-E), and role signal (C). k=1.5 keeps the sigmoid in a
useful range for typical cosine margins of ±0.05..0.35, avoiding the
saturation bug of k=10.

A 1D convolution across the anchor axis smooths neighbour score disagreements
before max-pooling.
"""

from __future__ import annotations

import asyncio
import math
from typing import TYPE_CHECKING

import numpy as np

from job_ftch.application.graph.params import float_param

if TYPE_CHECKING:
    from job_ftch.domain import JobRecord


def _sparse_dot(job: dict[int, float], anchor: dict[int, float]) -> float:
    """Normalised sparse dot product for lexical similarity."""
    if not job or not anchor:
        return 0.0
    raw = sum(job.get(k, 0.0) * v for k, v in anchor.items())
    norm_job = float(np.sqrt(sum(v * v for v in job.values())))
    norm_anc = float(np.sqrt(sum(v * v for v in anchor.values())))
    denom = norm_job * norm_anc
    return raw / denom if denom > 1e-9 else 0.0


def _conv1d(scores: np.ndarray, kernel: tuple[float, float, float]) -> np.ndarray:
    """Reflect-padded 1D convolution along the anchor axis."""
    if scores.size == 0:
        return scores
    k0, k1, k2 = kernel
    padded = np.concatenate([[scores[0]], scores, [scores[-1]]])
    return np.array(
        [k0 * padded[i] + k1 * padded[i + 1] + k2 * padded[i + 2] for i in range(len(scores))],
        dtype=np.float32,
    )


class ParallelScoringNode:
    """Scores a JobRecord via parallel async branches, combines into margin-sigmoid.

    Reads bgem3_dense and bgem3_sparse from item.metadata (set by BgeMThreeNode).
    Stores per-branch scores and parallel_final_score in metadata.
    Runs alongside MultiProfileMatchNode; RoutingNode prefers this score when present.
    """

    def __init__(
        self,
        pos_dense: np.ndarray,
        neg_dense: np.ndarray,
        pos_sparse: list[dict[int, float]],
        role_anchors: list[np.ndarray],
        neg_sparse: list[dict[int, float]] | None = None,
        *,
        w_dense: float = 0.50,
        w_sparse: float = 0.25,
        w_role: float = 0.25,
        margin_k: float = 20.0,
        conv_kernel: tuple[float, float, float] = (0.25, 0.50, 0.25),
    ) -> None:
        self._pos_dense = pos_dense
        self._neg_dense = neg_dense
        self._pos_sparse = pos_sparse
        self._neg_sparse = neg_sparse or []
        self._role_anchors = (
            np.vstack(role_anchors) if role_anchors else np.zeros((0, 1024), np.float32)
        )
        self._w_dense = w_dense
        self._w_sparse = w_sparse
        self._w_role = w_role
        self._margin_k = margin_k
        self._conv_kernel = conv_kernel

    def configure_graph_params(self, params: dict[str, object]) -> None:
        for name, attr in (
            ("w_dense", "_w_dense"),
            ("w_sparse", "_w_sparse"),
            ("w_role", "_w_role"),
        ):
            if name in params:
                setattr(self, attr, float_param(params, name, getattr(self, attr)))
        if "margin_k" in params:
            self._margin_k = float_param(params, "margin_k", self._margin_k)

    async def process(self, item: JobRecord) -> JobRecord:
        dense_raw = item.metadata.get("bgem3_dense")
        sparse_raw = item.metadata.get("bgem3_sparse")
        if dense_raw is None:
            return item

        dense = np.asarray(dense_raw, dtype=np.float32)
        sparse: dict[int, float] = sparse_raw or {}

        score_a, score_b, score_c, neg_sim, neg_sparse_sim = await asyncio.gather(
            self._branch_dense(dense),
            self._branch_sparse(sparse),
            self._branch_role(dense),
            self._branch_neg_dense(dense),
            self._branch_neg_sparse(sparse),
        )

        dense_margin = score_a - neg_sim
        w_sparse_eff = self._w_sparse if any(self._pos_sparse) else 0.0
        sparse_margin = score_b - neg_sparse_sim

        # Fuse only the CONTRASTIVE signals (pos-vs-neg margins). The role
        # branch (score_c) is an absolute cosine ~0.5 for any tech post, so
        # adding it injected a constant pedestal that flattened the sigmoid to
        # ~0.55 for every item and buried the discriminative dense margin. It
        # is kept below as an observability metric and tiebreaker only.
        # ``w_dense`` is now actually applied (previously dense entered at an
        # implicit weight of 1.0, making the parameter a silent no-op).
        margin = self._w_dense * dense_margin + w_sparse_eff * sparse_margin
        final = 1.0 / (1.0 + math.exp(-self._margin_k * margin))

        return item.model_copy(
            update={
                "metadata": {
                    **item.metadata,
                    "parallel_score_dense": round(float(score_a), 4),
                    "parallel_score_sparse": round(float(score_b), 4),
                    "parallel_score_role": round(float(score_c), 4),
                    "parallel_neg_sim": round(float(neg_sim), 4),
                    "parallel_dense_margin": round(float(dense_margin), 4),
                    "parallel_sparse_margin": round(float(sparse_margin), 4),
                    "parallel_final_score": round(float(final), 4),
                }
            }
        )

    async def _branch_dense(self, dense: np.ndarray) -> float:
        if self._pos_dense.size == 0:
            return 0.0
        sims = self._pos_dense @ dense
        smoothed = _conv1d(sims, self._conv_kernel)
        return float(np.max(smoothed))

    async def _branch_sparse(self, sparse: dict[int, float]) -> float:
        if not self._pos_sparse or not sparse:
            return 0.0
        scores = np.array(
            [_sparse_dot(sparse, anchor) for anchor in self._pos_sparse], dtype=np.float32
        )
        smoothed = _conv1d(scores, self._conv_kernel)
        return float(np.max(smoothed))

    async def _branch_role(self, dense: np.ndarray) -> float:
        if self._role_anchors.size == 0:
            return 0.0
        sims = self._role_anchors @ dense
        smoothed = _conv1d(sims, self._conv_kernel)
        return float(np.max(smoothed))

    async def _branch_neg_dense(self, dense: np.ndarray) -> float:
        if self._neg_dense.size == 0:
            return 0.0
        sims = self._neg_dense @ dense
        return float(np.max(sims))

    async def _branch_neg_sparse(self, sparse: dict[int, float]) -> float:
        if not self._neg_sparse or not sparse:
            return 0.0
        scores = np.array(
            [_sparse_dot(sparse, anchor) for anchor in self._neg_sparse], dtype=np.float32
        )
        return float(np.max(scores))
