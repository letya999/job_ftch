"""Typed contracts for the experiment graph control plane."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ExecutionMode(StrEnum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    BACKGROUND = "background"
    DEFERRED = "deferred"
    POST_ACCEPT = "post_accept"


class EffectMode(StrEnum):
    OBSERVE = "observe"
    GATE = "gate"
    STATEFUL_CHECKPOINT = "stateful_checkpoint"
    TERMINAL_DECISION = "terminal_decision"
    SIDE_EFFECT = "side_effect"


class AuthorityMode(StrEnum):
    """Whether a node may influence control flow."""

    OBSERVE = "observe"
    SHADOW = "shadow"
    GATE = "gate"
    TERMINAL = "terminal"


class NodeOutcome(StrEnum):
    PASS = "pass"
    WOULD_DROP = "would_drop"
    DROP = "drop"
    ACCEPT_RECOMMENDATION = "accept_recommendation"
    REJECT_RECOMMENDATION = "reject_recommendation"
    REVIEW_RECOMMENDATION = "review_recommendation"
    UNKNOWN = "unknown"
    ERROR = "error"
    TIMEOUT = "timeout"
    DEFERRED = "deferred"


@dataclass(frozen=True)
class ParamSpec:
    """Small declarative schema for parameters exposed to graph experiments."""

    type_name: str
    default: Any = None
    minimum: float | None = None
    maximum: float | None = None
    enum: tuple[Any, ...] = ()
    required: bool = False

    def validate(self, name: str, value: Any) -> Any:
        if self.type_name == "bool" and not isinstance(value, bool):
            raise ValueError(f"parameter {name} must be bool")
        if self.type_name == "int" and (isinstance(value, bool) or not isinstance(value, int)):
            raise ValueError(f"parameter {name} must be int")
        if self.type_name == "float" and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise ValueError(f"parameter {name} must be float")
        if self.type_name == "str" and not isinstance(value, str):
            raise ValueError(f"parameter {name} must be str")
        if self.enum and value not in self.enum:
            raise ValueError(f"parameter {name} must be one of {list(self.enum)!r}")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if self.minimum is not None and value < self.minimum:
                raise ValueError(f"parameter {name} must be >= {self.minimum}")
            if self.maximum is not None and value > self.maximum:
                raise ValueError(f"parameter {name} must be <= {self.maximum}")
        return value


@dataclass(frozen=True)
class EvidencePatch:
    """Immutable signal emitted by a non-terminal producer."""

    claim: str
    producer: str
    independence_group: str
    features: dict[str, Any] = field(default_factory=dict)
    recommendation: str = "unknown"
    reliability: str = "default"
    reason: str | None = None


@dataclass(frozen=True)
class EvidenceBundle:
    patches: tuple[EvidencePatch, ...] = ()

    def merge(self, *patches: EvidencePatch) -> EvidenceBundle:
        merged = list(self.patches)
        seen = {
            (patch.claim, patch.producer, patch.independence_group, patch.reliability)
            for patch in merged
        }
        for patch in patches:
            key = (patch.claim, patch.producer, patch.independence_group, patch.reliability)
            if key not in seen:
                merged.append(patch)
                seen.add(key)
        return EvidenceBundle(
            tuple(
                sorted(
                    merged,
                    key=lambda item: (
                        item.claim,
                        item.independence_group,
                        item.producer,
                        item.reliability,
                    ),
                )
            )
        )


@dataclass(frozen=True)
class NodeManifest:
    node_id: str
    factory: str
    input_type: str = "Any"
    output_type: str = "Any"
    implementation_version: str = "1"
    capabilities: tuple[str, ...] = ()
    allowed_execution: tuple[str, ...] = (ExecutionMode.SEQUENTIAL,)
    allowed_effects: tuple[str, ...] = (EffectMode.OBSERVE,)
    requires: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    mutates_payload: bool = False
    side_effects: bool = False
    deterministic: bool = True
    cacheable: bool = False
    batchable: bool = False
    timeout_ms: int = 30000
    failure_policy: str = "fail"
    cost_class: str = "free"
    tenant_sensitive: bool = False
    profile_sensitive: bool = False
    terminal_eligible: bool = False
    param_schema: dict[str, ParamSpec] = field(default_factory=dict)
    resource_requirements: tuple[str, ...] = ()
    decision_capability: bool = False
    evidence_claims: tuple[str, ...] = ()


@dataclass(frozen=True)
class NodeSpec:
    id: str
    node: str
    enabled: bool = True
    execution: str = ExecutionMode.SEQUENTIAL
    effect: str = EffectMode.OBSERVE
    after: tuple[str, ...] = ()
    start_after: tuple[str, ...] = ()
    join_at: str | None = None
    timeout_ms: int | None = None
    on_error: str | None = None
    shadow: bool = False
    authority: str | None = None
    run_if: dict[str, Any] | None = None
    on_unknown_condition: str = "run"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphSpec:
    name: str
    version: str
    nodes: tuple[NodeSpec, ...]
    metadata: dict[str, Any] = field(default_factory=dict)
    resources: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeContext:
    """Typed boundary between declarative graph config and runtime resources.

    YAML may name a resource, but never owns a connection, secret, model or
    user payload.  ``node_instances`` is intentionally keyed by graph node id;
    it is the explicit escape hatch for already-composed production nodes and
    avoids implicit class-name matching.
    """

    resources: dict[str, Any] = field(default_factory=dict)
    node_instances: dict[str, Any] = field(default_factory=dict)
    factories: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def require(self, name: str) -> Any:
        if name not in self.resources:
            raise KeyError(f"runtime resource is not configured: {name}")
        return self.resources[name]


@dataclass(frozen=True)
class CompiledGraph:
    spec: GraphSpec
    manifests: tuple[NodeManifest, ...]
    graph_hash: str
    execution_order: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.spec.name,
            "version": self.spec.version,
            "graph_hash": self.graph_hash,
            "execution_order": list(self.execution_order),
            "metadata": self.spec.metadata,
            "resources": self.spec.resources,
            "nodes": [
                {
                    "id": n.id,
                    "node": n.node,
                    "enabled": n.enabled,
                    "execution": n.execution,
                    "effect": n.effect,
                    "after": list(n.after),
                    "start_after": list(n.start_after),
                    "join_at": n.join_at,
                    "timeout_ms": n.timeout_ms,
                    "on_error": n.on_error,
                    "shadow": n.shadow,
                    "authority": n.authority
                    or (
                        "shadow"
                        if n.shadow
                        else "gate"
                        if n.effect == EffectMode.GATE
                        else "terminal"
                        if n.effect == EffectMode.TERMINAL_DECISION
                        else "observe"
                    ),
                    "run_if": n.run_if,
                    "on_unknown_condition": n.on_unknown_condition,
                    "params": n.params,
                }
                for n in self.spec.nodes
            ],
        }
