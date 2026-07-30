"""Stable graph inspection formats used by CLI and manifests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .contracts import CompiledGraph


def print_graph(graph: CompiledGraph, format: str = "json") -> str:
    if format == "json":
        return json.dumps(graph.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    if format == "dot":
        lines = [f'digraph "{graph.spec.name}" {{']
        for node in graph.spec.nodes:
            lines.append(f'  "{node.id}" [label="{node.id}\\n{node.execution}/{node.effect}"];')
            for dep in (*node.after, *node.start_after):
                lines.append(f'  "{dep}" -> "{node.id}";')
        lines.append("}")
        return "\n".join(lines)
    raise ValueError(f"unsupported graph format: {format}")
