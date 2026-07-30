from __future__ import annotations

import asyncio
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path


def _module() -> object:
    spec = importlib.util.spec_from_file_location(
        "run_pipeline_eval", Path("scripts/eval/run_pipeline_eval.py")
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass
class _Item:
    stable_id: str
    routing_decision: str | None = None
    review_reasons: tuple[str, ...] = ()


@dataclass
class _Report:
    item: _Item
    status: str
    node_stats: dict[str, dict[str, float]] = field(
        default_factory=lambda: {"node": {"elapsed_ms": 1.0}}
    )


class _Executor:
    async def run_many(self, _item: object) -> list[_Report]:
        return [
            _Report(_Item("candidate-a", "accept"), "ACCEPT"),
            _Report(_Item("candidate-b"), "REJECT"),
        ]


def test_graph_eval_preserves_one_result_per_candidate() -> None:
    result = asyncio.run(
        _module()._run_item_graph({"stable_id": "parent", "text": "job"}, _Executor())
    )  # type: ignore[attr-defined]
    assert [row["candidate_id"] for row in result["candidate_results"]] == [
        "candidate-a",
        "candidate-b",
    ]
    assert result["pipeline_accepted"] is True
