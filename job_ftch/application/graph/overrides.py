"""Apply validated CLI graph overrides without mutating the loaded preset."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING

from .contracts import GraphSpec, NodeSpec

if TYPE_CHECKING:
    from collections.abc import Sequence


def apply_overrides(
    spec: GraphSpec,
    *,
    params: Sequence[str] = (),
    enable: Sequence[str] = (),
    disable: Sequence[str] = (),
    effects: Sequence[str] = (),
    executions: Sequence[str] = (),
) -> GraphSpec:
    nodes = {node.id: node for node in spec.nodes}
    if len(nodes) != len(spec.nodes):
        raise ValueError("graph node ids must be unique")
    enabled_overrides: dict[str, bool] = {}
    effect_overrides: dict[str, str] = {}
    execution_overrides: dict[str, str] = {}
    param_overrides: dict[str, dict[str, object]] = {}
    for node_id in enable:
        _require(nodes, node_id)
        enabled_overrides[node_id] = True
    for node_id in disable:
        _require(nodes, node_id)
        enabled_overrides[node_id] = False
    for raw in effects:
        node_id, value = _assignment(raw)
        _require(nodes, node_id)
        effect_overrides[node_id] = value
    for raw in executions:
        node_id, value = _assignment(raw)
        _require(nodes, node_id)
        execution_overrides[node_id] = value
    for raw in params:
        left, value = _assignment(raw)
        node_id, dot, key = left.partition(".")
        if not dot or not key:
            raise ValueError(f"parameter override must be NODE.PARAM=VALUE: {raw!r}")
        node = _require(nodes, node_id)
        merged = param_overrides.setdefault(node_id, dict(node.params))
        merged[key] = _parse_value(value)
    return GraphSpec(
        spec.name,
        spec.version,
        tuple(
            replace(
                node,
                enabled=enabled_overrides.get(node.id, node.enabled),
                effect=effect_overrides.get(node.id, node.effect),
                execution=execution_overrides.get(node.id, node.execution),
                params=param_overrides.get(node.id, node.params),
            )
            for node in spec.nodes
        ),
        spec.metadata,
        spec.resources,
    )


def _require(nodes: dict[str, NodeSpec], node_id: str) -> NodeSpec:
    try:
        return nodes[node_id]
    except KeyError as exc:
        raise ValueError(f"unknown graph node: {node_id}") from exc


def _assignment(raw: str) -> tuple[str, str]:
    left, separator, right = raw.partition("=")
    if not separator or not left or not right:
        raise ValueError(f"override must be KEY=VALUE: {raw!r}")
    return left, right


def _parse_value(value: str) -> object:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value
