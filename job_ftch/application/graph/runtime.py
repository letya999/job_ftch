"""Runtime composition for the typed YAML v2 evidence spine."""

from __future__ import annotations

from typing import Any

from .contracts import CompiledGraph, RuntimeContext
from .executor import GraphExecutor
from .registry import bind_node_instances


def build_v2_executor(
    graph: CompiledGraph,
    *,
    nodes: list[Any],
    sanitize_node: Any,
    catalog: Any,
    typed_bindings: dict[str, Any],
    tenant_id: str,
    user_id: str | None,
    runtime_resources: dict[str, Any] | None = None,
) -> GraphExecutor:
    """Compose the v2 graph from the same injected runtime dependencies.

    Node construction belongs to the application composition root. This
    module only binds already-injected stages to YAML ids and executes them.
    """

    class SnapshotFilterNode:
        async def process(self, item: Any) -> Any:
            return item

    noop_snapshot = SnapshotFilterNode()

    if graph.spec.metadata.get("graph_schema") != "v2":
        raise ValueError("build_v2_executor requires graph_schema=v2")

    available = bind_node_instances([sanitize_node, *nodes, noop_snapshot])
    bindings: dict[str, Any] = {}
    for graph_node in graph.spec.nodes:
        instance = available.get(graph_node.node)
        if instance is not None:
            bindings[graph_node.id] = instance

    for graph_node in graph.spec.nodes:
        instance = typed_bindings.get(graph_node.node)
        if instance is not None:
            bindings[graph_node.id] = instance

    resources = {
        "active_profile": catalog,
        **(runtime_resources or {}),
    }
    return GraphExecutor.from_runtime_context(
        graph,
        RuntimeContext(
            resources=resources,
            node_instances=bindings,
            metadata={"tenant_id": tenant_id, "user_id": user_id},
        ),
    )
