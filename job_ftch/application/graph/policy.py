"""Deterministic, declarative terminal-signal aggregation for graph experiments."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .contracts import EvidenceBundle, EvidencePatch


@dataclass(frozen=True)
class SignalContribution:
    name: str
    value: float
    weight: float
    independence_group: str
    source: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "weight": self.weight,
            "independence_group": self.independence_group,
            "source": self.source,
        }


class DecisionPolicy:
    """Evaluate only explicitly selected features; missing means unknown."""

    def __init__(self, config: dict[str, Any]) -> None:
        validate_policy(config)
        self.config = config
        if config.get("mode") not in {"legacy", "weighted", "claims"}:
            raise ValueError("decision policy mode must be weighted or claims")

    def evaluate(
        self, metadata: dict[str, Any], evidence: EvidenceBundle | None = None
    ) -> dict[str, Any]:
        if evidence is None:
            raw_evidence = metadata.get("_graph_evidence", [])
            if isinstance(raw_evidence, list):
                evidence = EvidenceBundle(
                    tuple(
                        EvidencePatch(
                            claim=str(item.get("claim", "")),
                            producer=str(item.get("producer", "")),
                            independence_group=str(item.get("independence_group", "unknown")),
                            features=dict(item.get("features") or {}),
                            recommendation=str(item.get("recommendation", "unknown")),
                            reliability=str(item.get("reliability", "default")),
                            reason=item.get("reason"),
                        )
                        for item in raw_evidence
                        if isinstance(item, dict)
                    )
                )
        claim = self._claim_config()
        feature_specs = claim.get("features") or {}
        if not isinstance(feature_specs, dict):
            raise ValueError("decision policy features must be a mapping")
        evidence_values: dict[str, tuple[Any, str, str]] = {}
        for patch in evidence.patches if evidence else ():
            for name, value in patch.features.items():
                # Keep the first deterministic producer for an exact feature;
                # duplicate ideas in one independence group are not summed.
                key = str(name)
                candidate = (value, patch.independence_group, patch.producer)
                previous = evidence_values.get(key)
                if previous is None or (candidate[1], candidate[2]) < (previous[1], previous[2]):
                    evidence_values[key] = candidate
        contributions: list[SignalContribution] = []
        missing: list[str] = []
        for name, raw_spec in feature_specs.items():
            spec = raw_spec if isinstance(raw_spec, dict) else {}
            value = metadata.get(name)
            group = str(spec.get("independence_group", f"metadata:{name}"))
            source = "metadata"
            if value is None and name == "llm_relevance":
                value = (metadata.get("_llm_relevance") or {}).get("confidence")
            if value is None and name in evidence_values:
                value, group, source = evidence_values[name]
            transformed = _transform(value, str(spec.get("transform", "identity")))
            if transformed is None:
                missing.append(str(name))
                continue
            contributions.append(
                SignalContribution(
                    name=str(name),
                    value=transformed,
                    weight=float(spec.get("weight", 1.0)),
                    independence_group=group,
                    source=source,
                )
            )
        score = _aggregate(contributions, str(claim.get("aggregation", "weighted_mean")))
        accept_threshold = float(
            claim.get("accept_threshold", self.config.get("accept_threshold", 0.55))
        )
        review_threshold = float(
            claim.get("review_threshold", self.config.get("review_threshold", 0.5))
        )
        vetoes = _evaluate_vetoes(metadata, claim.get("vetoes", self.config.get("vetoes", [])))
        rescue = _evaluate_rescue(metadata, evidence, claim.get("rescue"))
        if rescue:
            score = max(score, accept_threshold)
        decision = "accept" if score >= accept_threshold else "reject"
        if vetoes:
            decision = "reject"
        return {
            "mode": self.config.get("mode"),
            "claim": claim.get("name", "profile_relevance"),
            "score": round(score, 8),
            "accept_threshold": accept_threshold,
            "review_threshold": review_threshold,
            "decision": decision,
            "review_band": review_threshold <= score < accept_threshold,
            "vetoes": vetoes,
            "rescue": rescue,
            "signals": [item.as_dict() for item in contributions],
            "missing": missing,
        }

    def _claim_config(self) -> dict[str, Any]:
        claims = self.config.get("claims")
        if isinstance(claims, dict) and claims:
            name, config = next(iter(claims.items()))
            if not isinstance(config, dict):
                raise ValueError(f"decision policy claim {name!r} must be a mapping")
            return {"name": name, **config}
        signals = self.config.get("signals", [])
        features = {
            str(item.get("name")): {
                "weight": item.get("weight", 1.0),
                "transform": item.get("transform", "identity"),
            }
            for item in signals
            if isinstance(item, dict) and item.get("name")
        }
        return {
            "name": "profile_relevance",
            "features": features,
            "aggregation": self.config.get("aggregation", "weighted_mean"),
            "accept_threshold": self.config.get("accept_threshold", 0.55),
            "review_threshold": self.config.get("review_threshold", 0.5),
            "vetoes": self.config.get("vetoes", []),
        }


def _transform(value: Any, transform: str) -> float | None:
    if transform == "boolean":
        return 1.0 if bool(value) else 0.0 if value is not None else None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    numeric = float(value)
    if transform == "identity":
        return numeric
    if transform == "clamp01":
        return max(0.0, min(1.0, numeric))
    if transform == "sigmoid":
        return 1.0 / (1.0 + math.exp(-numeric))
    raise ValueError(f"unsupported decision transform: {transform}")


def _aggregate(contributions: list[SignalContribution], aggregation: str) -> float:
    if not contributions:
        return 0.0
    # One independence group contributes at most once.  We retain the highest
    # weighted signal in a group for max/weighted policies.
    grouped: dict[str, SignalContribution] = {}
    for item in contributions:
        new = grouped.get(item.independence_group)
        if new is None or item.value * item.weight > new.value * new.weight:
            grouped[item.independence_group] = item
    values = list(grouped.values())
    if aggregation == "max":
        return max(item.value for item in values)
    if aggregation == "min":
        return min(item.value for item in values)
    if aggregation == "any":
        return 1.0 if any(item.value > 0 for item in values) else 0.0
    if aggregation == "all":
        return 1.0 if all(item.value > 0 for item in values) else 0.0
    if aggregation != "weighted_mean":
        raise ValueError(f"unsupported decision aggregation: {aggregation}")
    weight = sum(item.weight for item in values)
    return sum(item.value * item.weight for item in values) / weight if weight else 0.0


def _evaluate_vetoes(metadata: dict[str, Any], vetoes: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for veto in vetoes if isinstance(vetoes, list) else []:
        if not isinstance(veto, dict):
            continue
        name = str(veto.get("name", ""))
        value = metadata.get(name)
        triggered = False
        if "lt" in veto and isinstance(value, (int, float)):
            triggered = value < float(veto["lt"])
        elif "equals" in veto:
            triggered = value == veto["equals"]
        if triggered:
            result.append({"name": name, "value": value, "rule": veto})
    return result


def _evaluate_rescue(
    metadata: dict[str, Any], evidence: EvidenceBundle | None, rescue: Any
) -> bool:
    if not rescue:
        return False
    if rescue == "applied_ai_strong_without_non_target":
        return bool(metadata.get("applied_ai_action")) and not bool(
            metadata.get("strict_non_target")
        )
    if rescue == "any_positive":
        return any(
            p.recommendation in {"support", "accept"}
            for p in (evidence.patches if evidence else ())
        )
    return False


def validate_policy(config: dict[str, Any]) -> None:
    if not isinstance(config, dict):
        raise ValueError("decision_policy must be a mapping")
    if config.get("mode") == "legacy":
        return
    if config.get("mode") not in {"weighted", "claims"}:
        raise ValueError("decision policy mode must be weighted or claims")
    claims = config.get("claims")
    if claims is not None and not isinstance(claims, dict):
        raise ValueError("decision policy claims must be a mapping")
    sources = claims.values() if claims else (config,)
    for claim in sources:
        if not isinstance(claim, dict):
            raise ValueError("each decision policy claim must be a mapping")
        features = claim.get("features", {})
        if not isinstance(features, dict):
            raise ValueError("decision policy features must be a mapping")
        for name, spec in features.items():
            if not isinstance(name, str) or not name:
                raise ValueError("decision policy feature names must be non-empty strings")
            if not isinstance(spec, dict):
                raise ValueError(f"decision policy feature {name!r} must be a mapping")
            weight = spec.get("weight", 1.0)
            if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight < 0:
                raise ValueError(f"decision policy feature {name!r} has invalid weight")
        aggregation = claim.get("aggregation", config.get("aggregation", "weighted_mean"))
        if aggregation not in {"weighted_mean", "max", "min", "any", "all"}:
            raise ValueError(f"unsupported decision aggregation: {aggregation}")
