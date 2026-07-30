"""Compile and validate graph specs before dependencies or data are loaded."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from dataclasses import replace

from .conditions import validate_condition
from .contracts import CompiledGraph, EffectMode, ExecutionMode, GraphSpec, NodeSpec
from .policy import validate_policy
from .registry import manifest, validate_params

_LEGACY_86BC192 = "legacy_86bc192"


class GraphCompiler:
    def compile(self, spec: GraphSpec) -> CompiledGraph:
        if isinstance(spec.metadata.get("decision_policy"), dict):
            validate_policy(spec.metadata["decision_policy"])
        enabled = [_normalize_authority(node) for node in spec.nodes if node.enabled]
        if not enabled or enabled[0].node != "sanitize":
            raise ValueError("SanitizeNode must be the first enabled graph node")
        ids = [node.id for node in spec.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("graph node ids must be unique")
        is_v2 = spec.metadata.get("graph_schema") == "v2"
        if is_v2:
            unsupported = [
                node.id
                for node in enabled
                if node.execution in (ExecutionMode.PARALLEL, ExecutionMode.BACKGROUND)
                or node.start_after
                or node.join_at is not None
            ]
            if unsupported:
                raise ValueError(
                    "graph_schema=v2 is a linear typed spine; concurrent evidence "
                    f"must be internal to EvidenceFanOutNode (nodes: {unsupported})"
                )
        id_set = set(ids)
        enabled_ids = {node.id for node in enabled}
        for node in enabled:
            unknown = (set(node.after) | set(node.start_after)) - id_set
            if unknown:
                raise ValueError(f"{node.id}: unknown dependency {sorted(unknown)}")
            disabled = (set(node.after) | set(node.start_after)) - enabled_ids
            if disabled:
                raise ValueError(f"{node.id}: dependency is disabled {sorted(disabled)}")
        enabled = [
            replace(node, params=validate_params(node.node, node.params)) for node in enabled
        ]
        manifests = tuple(manifest(node.node) for node in enabled)
        declared_resources = set(spec.resources)
        for node, item in zip(enabled, manifests, strict=True):
            missing_resources = set(item.resource_requirements) - declared_resources
            if missing_resources:
                raise ValueError(f"{node.id}: graph resources missing {sorted(missing_resources)}")
        for node, item in zip(enabled, manifests, strict=True):
            if node.run_if is None:
                continue
            validate_condition(node.run_if)
            if node.on_unknown_condition not in {"run", "skip"}:
                raise ValueError(f"{node.id}: on_unknown_condition must be run or skip")
            if item.input_type != item.output_type:
                raise ValueError(f"{node.id}: conditional stages must preserve payload type")
        terminal = [
            node
            for node, item in zip(enabled, manifests, strict=True)
            if node.effect == EffectMode.TERMINAL_DECISION
        ]
        if len(terminal) != 1:
            raise ValueError(
                f"graph must have exactly one terminal_decision owner, got {len(terminal)}"
            )
        if is_v2 and terminal[0].node != "decision":
            raise ValueError("graph_schema=v2 requires DecisionNode as terminal owner")
        manifest_by_id = {node.id: item for node, item in zip(enabled, manifests, strict=True)}
        for node, item in zip(enabled, manifests, strict=True):
            if node.authority is not None and node.authority not in {
                "observe",
                "shadow",
                "gate",
                "terminal",
            }:
                raise ValueError(f"{node.id}: invalid authority {node.authority!r}")
            if node.authority == "terminal" and node.effect != EffectMode.TERMINAL_DECISION:
                raise ValueError(f"{node.id}: authority=terminal requires terminal_decision effect")
            if node.execution not in item.allowed_execution:
                raise ValueError(f"{node.id}: execution {node.execution} is not allowed")
            if node.effect not in item.allowed_effects:
                raise ValueError(f"{node.id}: effect {node.effect} is not allowed")
            if item.mutates_payload and node.execution not in (
                ExecutionMode.SEQUENTIAL,
                ExecutionMode.POST_ACCEPT,
            ):
                raise ValueError(
                    f"{node.id}: payload-mutating nodes must be sequential or post_accept"
                )
            if node.effect == EffectMode.SIDE_EFFECT and not terminal:
                raise ValueError(f"{node.id}: side effects require a terminal decision")
            if (
                node.execution in (ExecutionMode.PARALLEL, ExecutionMode.BACKGROUND)
                and "evidence_producer" not in item.capabilities
            ):
                raise ValueError(f"{node.id}: parallel/background nodes must be evidence producers")
            if (
                node.effect == EffectMode.GATE
                and not node.shadow
                and "gate" not in item.capabilities
            ):
                raise ValueError(f"{node.id}: gate effect requires a gate-capable manifest")
            if node.join_at and node.join_at not in set(spec.metadata.get("barriers", ())):
                raise ValueError(f"{node.id}: unknown barrier {node.join_at!r}")
            if node.execution == ExecutionMode.SEQUENTIAL and len(node.after) == 1:
                previous = manifest_by_id[node.after[0]]
                fanout_materialization = (
                    "fan_out" in previous.capabilities and item.input_type == "RawItem"
                )
                if (
                    not fanout_materialization
                    and previous.output_type != item.input_type
                    and previous.output_type != "Any"
                    and item.input_type != "Any"
                ):
                    raise ValueError(
                        f"{node.id}: {previous.output_type} cannot feed {item.input_type}"
                    )
        order = _topological_order(enabled)
        if not _is_reachable_from_sanitize(terminal[0].id, enabled):
            raise ValueError("terminal decision must be reachable from sanitize")
        if any(
            node.effect == EffectMode.SIDE_EFFECT
            and order.index(node.id) < order.index(terminal[0].id)
            for node in enabled
        ):
            raise ValueError("side effects must be after terminal decision")
        if is_v2 and any(
            node.effect == EffectMode.SIDE_EFFECT and node.execution != ExecutionMode.POST_ACCEPT
            for node in enabled
        ):
            raise ValueError("graph_schema=v2 side effects must use post_accept execution")
        checkpoints = [node for node in enabled if node.effect == EffectMode.STATEFUL_CHECKPOINT]
        if checkpoints:
            if spec.metadata.get("compatibility_mode") != _LEGACY_86BC192:
                raise ValueError("stateful checkpoints require compatibility_mode=legacy_86bc192")
            if len(checkpoints) != 1:
                raise ValueError("legacy_86bc192 permits exactly one stateful checkpoint")
            checkpoint = checkpoints[0]
            if checkpoint.id != "aggregation" or checkpoint.node != "aggregation":
                raise ValueError(
                    "legacy_86bc192 permits only aggregation as its stateful checkpoint"
                )
            if order.index(checkpoint.id) >= order.index(terminal[0].id):
                raise ValueError("legacy aggregation checkpoint must precede terminal decision")
        payload = {
            "name": spec.name,
            "version": spec.version,
            "metadata": spec.metadata,
            "resources": spec.resources,
            "nodes": [node.__dict__ for node in enabled],
            "implementations": {
                node.id: item.implementation_version
                for node, item in zip(enabled, manifests, strict=True)
            },
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
        ).hexdigest()
        return CompiledGraph(
            spec=GraphSpec(spec.name, spec.version, tuple(enabled), spec.metadata, spec.resources),
            manifests=manifests,
            graph_hash=digest,
            execution_order=tuple(order),
        )


def _topological_order(nodes: list[NodeSpec]) -> list[str]:
    edges: dict[str, set[str]] = defaultdict(set)
    indegree = {node.id: 0 for node in nodes}
    for node in nodes:
        deps = set(node.after) | set(node.start_after)
        for dep in deps:
            if node.id not in edges[dep]:
                edges[dep].add(node.id)
                indegree[node.id] += 1
    queue = deque(node.id for node in nodes if indegree[node.id] == 0)
    result: list[str] = []
    while queue:
        current = queue.popleft()
        result.append(current)
        for child in sorted(edges[current]):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if len(result) != len(nodes):
        raise ValueError("graph dependencies contain a cycle")
    return result


def _is_reachable_from_sanitize(target: str, nodes: list[NodeSpec]) -> bool:
    children: dict[str, set[str]] = defaultdict(set)
    for node in nodes:
        for dependency in (*node.after, *node.start_after):
            children[dependency].add(node.id)
    pending = ["sanitize"]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(children[current] - visited)
    return False


def _normalize_authority(node: NodeSpec) -> NodeSpec:
    if node.authority is None:
        return node
    if node.authority not in {"observe", "shadow", "gate", "terminal"}:
        raise ValueError(f"{node.id}: invalid authority {node.authority!r}")
    effect = {
        "observe": EffectMode.OBSERVE,
        "shadow": EffectMode.GATE,
        "gate": EffectMode.GATE,
        "terminal": EffectMode.TERMINAL_DECISION,
    }[node.authority]
    return replace(node, effect=effect, shadow=node.shadow or node.authority == "shadow")


def compile_graph(spec: GraphSpec) -> CompiledGraph:
    return GraphCompiler().compile(spec)
