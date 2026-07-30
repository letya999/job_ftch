"""Safe YAML loader for graph specifications."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .contracts import GraphSpec, NodeSpec


def _tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _load_data(source: Path, stack: tuple[Path, ...] = ()) -> dict[str, Any]:
    source = source.resolve()
    if source in stack:
        raise ValueError(f"graph extends cycle: {source}")
    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{source} must contain a mapping")
    parent_name = data.get("extends")
    if not parent_name:
        return data
    parent = _load_data(source.parent / str(parent_name), (*stack, source))
    merged = dict(parent)
    parent_nodes = {str(row["id"]): dict(row) for row in parent.get("nodes", [])}
    for row in data.get("nodes", []):
        if not isinstance(row, dict) or "id" not in row:
            raise ValueError(f"{source}: overlay nodes require id")
        node_id = str(row["id"])
        merged_node = {**parent_nodes.get(node_id, {}), **row}
        if "params" in row:
            merged_node["params"] = {
                **dict(parent_nodes.get(node_id, {}).get("params") or {}),
                **dict(row.get("params") or {}),
            }
        parent_nodes[node_id] = merged_node
    merged["nodes"] = list(parent_nodes.values())
    merged["metadata"] = {**dict(parent.get("metadata") or {}), **dict(data.get("metadata") or {})}
    merged["resources"] = {
        **dict(parent.get("resources") or {}),
        **dict(data.get("resources") or {}),
    }
    merged.update(
        {
            key: value
            for key, value in data.items()
            if key not in {"nodes", "metadata", "resources", "extends"}
        }
    )
    return merged


def load_graph(path: str | Path) -> GraphSpec:
    source = Path(path)
    data = _load_data(source)
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise ValueError(f"{source} must contain a nodes list")
    nodes = tuple(
        NodeSpec(
            id=str(row["id"]),
            node=str(row["node"]),
            enabled=bool(row.get("enabled", True)),
            execution=str(row.get("execution", "sequential")),
            effect=str(row.get("effect", "observe")),
            after=_tuple(row.get("after")),
            start_after=_tuple(row.get("start_after")),
            join_at=str(row["join_at"]) if row.get("join_at") is not None else None,
            timeout_ms=int(row["timeout_ms"]) if row.get("timeout_ms") is not None else None,
            on_error=str(row["on_error"]) if row.get("on_error") is not None else None,
            shadow=bool(row.get("shadow", False)),
            authority=str(row["authority"]) if row.get("authority") is not None else None,
            run_if=dict(row["run_if"]) if row.get("run_if") is not None else None,
            on_unknown_condition=str(row.get("on_unknown_condition", "run")),
            params=dict(row.get("params") or {}),
        )
        for row in data["nodes"]
    )
    resources = data.get("resources") or {}
    if not isinstance(resources, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in resources.items()
    ):
        raise ValueError(f"{source}: resources must be a string-to-string mapping")
    return GraphSpec(
        name=str(data.get("name", source.stem)),
        version=str(data.get("version", "1")),
        nodes=nodes,
        metadata=dict(data.get("metadata") or {}),
        resources=dict(resources),
    )
