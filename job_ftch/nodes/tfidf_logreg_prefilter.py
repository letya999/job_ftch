"""TF-IDF + logistic regression relevance prefilter without sklearn dependencies.

Proves only a negative: items with score below threshold are dropped before the
LLM judge. Never accepts - that remains the job of DecisionNode. Without a
trained model artifact the node degrades to pass-through so every candidate
reaches the LLM.
"""

from __future__ import annotations

import json
import logging
import math
import re
from hashlib import sha256
from pathlib import Path
from typing import TypedDict, cast

from opentelemetry import trace

from job_ftch.application.drops import RawItemDropped
from job_ftch.application.graph.params import float_param, str_param
from job_ftch.domain import (
    ClaimKind,
    EvidenceAtom,
    EvidencePolarity,
    EvidenceProvenance,
    RawItem,
    TriageRejectionReason,
    source_identity_for_raw_item,
)

_logger = logging.getLogger(__name__)
_tracer = trace.get_tracer("job_ftch.nodes")

# Matches sklearn TfidfVectorizer(analyzer="word") token pattern
_TOKEN_RE = re.compile(r"(?u)\b\w\w+\b")


class _PrefilterTrainingInfo(TypedDict, total=False):
    n_rows: int
    n_positive: int
    dataset_sha256: str


class TfidfLogregRelevancePrefilterNode:
    """TF-IDF + logistic regression prefilter to drop irrelevant items before LLM.

    Proves only a negative: score < threshold means drop. The node never
    accepts a candidate - that responsibility belongs to DecisionNode.

    Without a model artifact the node degrades to pass-through (every
    candidate reaches the LLM). This is an intentional safety choice:
    silently filtering on an absent or corrupt model is more dangerous
    than paying for extra LLM calls.
    """

    def __init__(
        self,
        threshold: float = 0.30,
        mode: str = "gate",
        model_path: str = "fixtures/prefilter/tfidf_logreg_v1.json",
    ) -> None:
        self._threshold = threshold
        self._mode = mode
        self._model_path = model_path

        self._vocabulary: dict[str, int] = {}
        self._idf: list[float] = []
        self._coef: list[float] = []
        self._intercept: float = 0.0
        self._model_version: str = "unknown"
        self._schema_version: int | None = None
        self._ngram_range: list[int] = [1, 2]
        self._sublinear_tf: bool = True
        self._degraded: str | None = None
        self._training_info: _PrefilterTrainingInfo = {}

        # Visible in runtime_node_stats so a degraded run cannot be
        # confused with a working one.
        self.stats: dict[str, object] = {
            "degraded": False,
            "degradation_reason": None,
            "items_passed": 0,
            "items_dropped": 0,
            "items_degraded_passthrough": 0,
        }

        self._load_model()

    def _load_model(self) -> None:
        try:
            path = Path(self._model_path)
            if not path.is_file():
                self._degraded = "model_missing"
                self.stats["degraded"] = True
                self.stats["degradation_reason"] = "model_missing"
                _logger.error(
                    "Relevance prefilter model not found: %s - degrading to pass-through",
                    self._model_path,
                )
                return

            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                self._degraded = "model_unreadable"
                self.stats["degraded"] = True
                self.stats["degradation_reason"] = "model_unreadable"
                _logger.error(
                    "Relevance prefilter model unreadable at %s: %s - degrading to pass-through",
                    self._model_path,
                    exc,
                )
                return

            if data.get("schema_version") != 1:
                self._degraded = "schema_version_mismatch"
                self.stats["degraded"] = True
                self.stats["degradation_reason"] = "schema_version_mismatch"
                _logger.error(
                    "Relevance prefilter schema_version=%s (expected 1) at %s"
                    " - degrading to pass-through",
                    data.get("schema_version"),
                    self._model_path,
                )
                return

            self._vocabulary = data["vocabulary"]
            self._idf = data["idf"]
            self._coef = data["coef"]
            self._intercept = float(data["intercept"])
            self._model_version = data.get("model_version", "unknown")
            self._schema_version = data.get("schema_version")
            self._ngram_range = data.get("ngram_range", [1, 2])
            self._sublinear_tf = data.get("sublinear_tf", True)
            training = data.get("training", {})
            self._training_info = (
                cast("_PrefilterTrainingInfo", training) if isinstance(training, dict) else {}
            )
            self._degraded = None
        except Exception as exc:
            _logger.error(
                "Failed to load relevance prefilter model from %s: %s - degrading to pass-through",
                self._model_path,
                exc,
            )
            self._degraded = "model_unreadable"
            self.stats["degraded"] = True
            self.stats["degradation_reason"] = "model_unreadable"

    def configure_graph_params(self, params: dict[str, object]) -> None:
        if "threshold" in params:
            self._threshold = float_param(params, "threshold", self._threshold)
        if "mode" in params:
            self._mode = str_param(params, "mode", self._mode)
        if "model_path" in params:
            new_path = str_param(params, "model_path", self._model_path)
            if new_path != self._model_path:
                self._model_path = new_path
                self._load_model()

    def preflight_status(self) -> dict[str, object]:
        """Return structured preflight information for this node."""
        status: dict[str, object] = {
            "enabled": True,
            "model_path": self._model_path,
            "model_present": self._degraded != "model_missing",
            "model_version": self._model_version,
            "schema_version": self._schema_version,
            "threshold": self._threshold,
            "trained_on": {
                "n_rows": self._training_info.get("n_rows"),
                "n_positive": self._training_info.get("n_positive"),
                "dataset_sha256": self._training_info.get("dataset_sha256"),
            },
            "status": "ok" if self._degraded is None else f"degraded:{self._degraded}",
        }
        return status

    def _extract_ngrams(self, text: str) -> list[str]:
        tokens = _TOKEN_RE.findall(text.lower())
        ngrams: list[str] = []
        min_n, max_n = self._ngram_range
        n_tokens = len(tokens)

        for n in range(min_n, min(max_n + 1, n_tokens + 1)):
            for i in range(n_tokens - n + 1):
                ngrams.append(" ".join(tokens[i : i + n]))
        return ngrams

    async def process(self, item: RawItem) -> RawItem | None:
        with _tracer.start_as_current_span("tfidf_logreg_prefilter.check") as span:
            span.set_attribute("job_ftch.node", "TfidfLogregRelevancePrefilterNode")

            if self._degraded is not None:
                span.set_attribute("job_ftch.node.result", "degraded")
                degraded_passthrough = self.stats.get("items_degraded_passthrough", 0)
                self.stats["items_degraded_passthrough"] = (
                    degraded_passthrough + 1 if isinstance(degraded_passthrough, int) else 1
                )
                metadata = dict(item.metadata)
                metadata["relevance_prefilter_degradation"] = self._degraded
                return item.model_copy(update={"metadata": metadata})

            # Inference
            ngrams = self._extract_ngrams(item.text)
            term_counts: dict[int, int] = {}
            for term in ngrams:
                if term in self._vocabulary:
                    idx = self._vocabulary[term]
                    term_counts[idx] = term_counts.get(idx, 0) + 1

            vec: list[tuple[int, float]] = []
            norm_sq = 0.0
            for idx, count in term_counts.items():
                tf = 1.0 + math.log(count) if self._sublinear_tf else float(count)
                val = tf * self._idf[idx]
                vec.append((idx, val))
                norm_sq += val * val

            norm = math.sqrt(norm_sq) if norm_sq > 0 else 1.0

            dot = self._intercept
            for idx, val in vec:
                dot += (val / norm) * self._coef[idx]

            # Sigmoid
            score = (
                1.0 / (1.0 + math.exp(-dot)) if dot >= 0 else math.exp(dot) / (1.0 + math.exp(dot))
            )

            span.set_attribute("job_ftch.tfidf_logreg_prefilter.score", float(score))
            span.set_attribute("job_ftch.tfidf_logreg_prefilter.threshold", float(self._threshold))
            span.set_attribute("job_ftch.tfidf_logreg_prefilter.model_version", self._model_version)

            metadata = dict(item.metadata)
            metadata["relevance_prefilter_score"] = float(score)
            metadata["relevance_prefilter_threshold"] = float(self._threshold)
            metadata["relevance_prefilter_decision"] = "drop" if score < self._threshold else "pass"
            metadata["relevance_prefilter_model_version"] = self._model_version

            if score < self._threshold:
                if self._mode == "shadow":
                    identity = source_identity_for_raw_item(item)
                    digest = sha256(str(score).encode()).hexdigest()[:16]

                    atom = EvidenceAtom(
                        evidence_id=f"{item.stable_id}:tfidf_logreg_prefilter:{digest}",
                        claim=ClaimKind.PROFILE_RELEVANCE,
                        subject="profile_relevance",
                        polarity=EvidencePolarity.CONTRADICTS,
                        strength=1.0,
                        reliability=0.9,
                        provenance=EvidenceProvenance.INFERRED,
                        producer="tfidf_logreg_prefilter",
                        producer_version=self._model_version,
                        source_family=identity.family,
                        observation_kind=identity.observation_kind,
                        transport=identity.transport,
                        independence_key=f"{item.stable_id}:tfidf_logreg_prefilter",
                        observation_id=item.stable_id,
                        candidate_id=str(metadata.get("candidate_span_id") or item.stable_id),
                        evidence_ref=f"tfidf_logreg_prefilter:{score:.4f}",
                    ).model_dump(mode="json")

                    metadata["evidence_atoms"] = [*metadata.get("evidence_atoms", []), atom]
                    span.set_attribute("job_ftch.node.result", "drop")
                    dropped = self.stats.get("items_dropped", 0)
                    self.stats["items_dropped"] = dropped + 1 if isinstance(dropped, int) else 1
                    return item.model_copy(update={"metadata": metadata})

                span.set_attribute("job_ftch.node.result", "drop")
                dropped = self.stats.get("items_dropped", 0)
                self.stats["items_dropped"] = dropped + 1 if isinstance(dropped, int) else 1
                dropped_item = item.model_copy(update={"metadata": metadata})
                raise RawItemDropped(
                    reason=TriageRejectionReason.LOW_RELEVANCE_PREFILTER,
                    details=(
                        "TF-IDF/logreg relevance score "
                        f"{score:.6f} is below threshold {self._threshold:.6f}"
                    ),
                    item=dropped_item,
                    stage="TfidfLogregRelevancePrefilterNode",
                )

            span.set_attribute("job_ftch.node.result", "pass")
            passed = self.stats.get("items_passed", 0)
            self.stats["items_passed"] = passed + 1 if isinstance(passed, int) else 1
            return item.model_copy(update={"metadata": metadata})
