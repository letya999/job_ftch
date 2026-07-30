"""Deterministic terminal aggregation of independent relevance signals."""

from __future__ import annotations

from job_ftch.application.graph.params import float_param
from job_ftch.domain import JobRecord, MatchDecision  # noqa: TC001


class DecisionAggregatorNode:
    """Make the final decision without another LLM call.

    The LLM is an evidence producer here.  Strong independent profile/BGE
    evidence can rescue an over-conservative LLM reject; mixed evidence goes
    to REVIEW instead of being silently discarded.
    """

    def __init__(
        self,
        *,
        accept_profile_score: float = 0.55,
        review_profile_score: float = 0.35,
        accept_llm_confidence: float = 0.55,
        allow_missing_llm_rescue: bool = True,
        allow_reject_rescue: bool = True,
        require_no_profile_conflict: bool = False,
    ) -> None:
        self._accept_profile_score = accept_profile_score
        self._review_profile_score = review_profile_score
        self._accept_llm_confidence = accept_llm_confidence
        self._allow_missing_llm_rescue = allow_missing_llm_rescue
        self._allow_reject_rescue = allow_reject_rescue
        self._require_no_profile_conflict = require_no_profile_conflict

    def configure_graph_params(self, params: dict[str, object]) -> None:
        self._accept_profile_score = float_param(
            params, "accept_profile_score", self._accept_profile_score
        )
        self._review_profile_score = float_param(
            params, "review_profile_score", self._review_profile_score
        )
        self._accept_llm_confidence = float_param(
            params, "accept_llm_confidence", self._accept_llm_confidence
        )
        self._allow_missing_llm_rescue = bool(
            params.get("allow_missing_llm_rescue", self._allow_missing_llm_rescue)
        )
        self._allow_reject_rescue = bool(
            params.get("allow_reject_rescue", self._allow_reject_rescue)
        )
        self._require_no_profile_conflict = bool(
            params.get("require_no_profile_conflict", self._require_no_profile_conflict)
        )

    async def process(self, item: JobRecord) -> JobRecord:
        metadata = dict(item.metadata)
        llm_raw = metadata.get("_llm_relevance")
        llm = llm_raw if isinstance(llm_raw, dict) else {}
        llm_decision = str(llm.get("decision") or "missing")
        llm_confidence = float(llm.get("confidence") or 0.0)
        profile_score = self._profile_score(item)
        bge_margin = self._number(metadata.get("semantic_prefilter_shot_margin"))
        semantic_margin = self._number(metadata.get("profile_semantic_margin"))
        lexical_negative = tuple(metadata.get("lexical_negative_matches") or ())
        profile_conflict = semantic_margin < 0.0 or bool(lexical_negative)
        hard_reject = self._hard_reject(item)

        if hard_reject:
            decision, reason = MatchDecision.REJECT, "hard_constraint_reject"
        elif (
            llm_decision == "accept"
            and llm_confidence >= self._accept_llm_confidence
            and not (self._require_no_profile_conflict and profile_conflict)
        ):
            decision, reason = MatchDecision.ACCEPT, "llm_accept"
        elif llm_decision == "accept" and self._require_no_profile_conflict and profile_conflict:
            decision, reason = MatchDecision.REVIEW, "llm_accept_profile_conflict"
        elif llm_decision == "missing" and not self._allow_missing_llm_rescue:
            decision, reason = MatchDecision.REVIEW, "missing_llm_evidence"
        elif llm_decision == "reject" and not self._allow_reject_rescue:
            decision, reason = MatchDecision.REJECT, "llm_reject"
        elif max(profile_score, bge_margin) >= self._accept_profile_score:
            decision, reason = MatchDecision.ACCEPT, "independent_signal_rescue"
        elif (
            llm_decision == "reject" and max(profile_score, bge_margin) < self._review_profile_score
        ):
            decision, reason = MatchDecision.REJECT, "llm_reject_low_independent_signal"
        else:
            decision, reason = MatchDecision.REVIEW, "conflicting_or_incomplete_evidence"

        trace = {
            "llm_decision": llm_decision,
            "llm_confidence": llm_confidence,
            "profile_score": profile_score,
            "bge_margin": bge_margin,
            "profile_semantic_margin": semantic_margin,
            "lexical_negative_matches": lexical_negative,
            "profile_conflict": profile_conflict,
            "hard_reject": hard_reject,
            "decision": decision.value,
            "reason": reason,
        }
        reasons = tuple(dict.fromkeys((*item.review_reasons, f"decision_aggregator:{reason}")))
        return item.model_copy(
            update={
                "routing_decision": decision,
                "review_reasons": reasons,
                "metadata": {**metadata, "decision_aggregator_trace": trace},
            }
        )

    @staticmethod
    def _number(value: object) -> float:
        return float(value) if isinstance(value, (int, float)) else 0.0

    @classmethod
    def _profile_score(cls, item: JobRecord) -> float:
        values = [
            cls._number(item.best_score),
            cls._number((item.metadata or {}).get("parallel_final_score")),
        ]
        return max(values, default=0.0)

    @staticmethod
    def _hard_reject(item: JobRecord) -> bool:
        states = (item.metadata or {}).get("hard_constraint_states")
        if not isinstance(states, dict) or not states:
            return False
        return any(
            isinstance(profile_states, dict)
            and any(value == "contradicted" for value in profile_states.values())
            for profile_states in states.values()
        )
