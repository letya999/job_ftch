"""Reranker node using native transformers for BAAI/bge-reranker-v2-m3."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from job_ftch.domain import JobRecord

_DEFAULT_MODEL_NAME = "BAAI/bge-reranker-v2-m3"
_DEFAULT_MODEL_REVISION = "refs/pr/6"


class BgeRerankerNode:
    """
    Scores the posting using a cross-encoder BGE reranker natively via transformers against target roles.
    Bypasses FlagEmbedding due to `prepare_for_model` deprecation in modern transformers.
    This node is available as an optional plug-in but is NOT wired into the default pipeline.
    The default scoring path uses ParallelScoringNode. Instantiate and inject this node
    explicitly if cross-encoder reranking is needed.
    """

    produced_metadata = frozenset({"bge_reranker_max_score"})

    def __init__(
        self,
        target_roles: list[str],
        model_name: str = _DEFAULT_MODEL_NAME,
        *,
        revision: str | None = None,
    ) -> None:
        self._target_roles = target_roles
        self._model: Any | None = None
        self._tokenizer: Any | None = None
        self._torch: Any | None = None
        model_revision = revision or os.environ.get(
            "JOB_FTCH_BGE_RERANKER_REVISION", _DEFAULT_MODEL_REVISION
        )

        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            self._torch = torch
            self._tokenizer = AutoTokenizer.from_pretrained(model_name, revision=model_revision)
            self._model = AutoModelForSequenceClassification.from_pretrained(
                model_name,
                revision=model_revision,
            )
            self._model.eval()

            if torch.cuda.is_available():
                self._model = self._model.half().cuda()
        except ImportError as exc:
            import structlog

            structlog.get_logger("bge_reranker").warning(
                "transformers_import_failed", error=str(exc)
            )
        except Exception as exc:
            import structlog

            structlog.get_logger("bge_reranker").warning("model_load_failed", error=str(exc))

    async def process(self, item: JobRecord) -> JobRecord:
        if self._model is None or self._tokenizer is None or not self._target_roles:
            return self._mark_unavailable(item, "model_or_target_roles_unavailable")

        torch = self._torch
        tokenizer = self._tokenizer
        model = self._model
        if torch is None:
            return self._mark_unavailable(item, "torch_unavailable")

        text = (item.title or "") + "\n" + (item.description or "")
        if not text.strip():
            return self._mark_unavailable(item, "candidate_text_unavailable")

        pairs = [[role, text] for role in self._target_roles]

        def _score() -> Any:
            with torch.no_grad():
                inputs = tokenizer(
                    pairs, padding=True, truncation=True, max_length=512, return_tensors="pt"
                )

                if torch.cuda.is_available():
                    inputs = {k: v.cuda() for k, v in inputs.items()}

                logits = model(**inputs, return_dict=True).logits.view(-1).float()

                # Sigmoid normalization (matches FlagReranker's normalize=True)
                return (1 / (1 + torch.exp(-logits))).cpu().tolist()

        try:
            # Tokenisation and the forward pass are blocking CPU work. Run them
            # off the event loop: the pipeline shares its loop with the Telegram
            # long-poll, and a blocking node starves `getUpdates` until the run
            # finishes, which looks to users like a dead bot.
            scores = await asyncio.to_thread(_score)

            if isinstance(scores, (float, int)):
                scores = [scores]

            max_score = max(scores) if scores else 0.0

            return item.model_copy(
                update={
                    "metadata": {
                        **item.metadata,
                        "bge_reranker_max_score": float(max_score),
                    }
                }
            )
        except Exception as exc:
            import structlog

            structlog.get_logger("bge_reranker").warning("rerank_failed", error=str(exc))
            return self._mark_unavailable(item, "rerank_failed")

    @staticmethod
    def _mark_unavailable(item: JobRecord, reason: str) -> JobRecord:
        return item.model_copy(
            update={
                "metadata": {
                    **item.metadata,
                    "reranker_degradation": reason,
                }
            }
        )
