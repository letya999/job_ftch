from __future__ import annotations

import pytest

from job_ftch.application.graph import compile_graph
from job_ftch.application.graph.conditions import ConditionResult, evaluate_condition
from job_ftch.application.graph.contracts import GraphSpec, NodeSpec
from job_ftch.application.graph.executor import GraphExecutor


class _Item:
    def __init__(self, value: float | None) -> None:
        self.assessment = {"score": value} if value is not None else {}


class _Claim:
    claim = "is_job"
    belief_true = 0.9


class _Sanitize:
    async def process(self, item: _Item) -> _Item:
        return item


class _Optional:
    def __init__(self) -> None:
        self.calls = 0

    async def process(self, item: _Item) -> _Item:
        self.calls += 1
        return item


class _Decision:
    async def process(self, item: _Item) -> dict[str, str]:
        return {"decision": "REVIEW"}


def _graph(*, conditional_node: str = "source_context"):
    return compile_graph(
        GraphSpec(
            "conditions",
            "1",
            (
                NodeSpec("sanitize", "sanitize", effect="gate"),
                NodeSpec(
                    "conditional",
                    conditional_node,
                    after=("sanitize",),
                    run_if={"gte": {"ref": "assessment.score", "value": 0.8}},
                ),
                NodeSpec("extraction", "extraction", after=("conditional",)),
                NodeSpec("normalization", "job_normalization", after=("extraction",)),
                NodeSpec("evidence", "evidence_fanout", after=("normalization",)),
                NodeSpec(
                    "decision",
                    "decision",
                    after=("evidence",),
                    effect="terminal_decision",
                ),
            ),
        )
    )


def test_condition_dsl_is_three_valued_and_never_reads_metadata() -> None:
    condition = {"gte": {"ref": "assessment.score", "value": 0.8}}
    assert evaluate_condition(condition, _Item(0.9)) is ConditionResult.TRUE
    assert evaluate_condition(condition, _Item(0.2)) is ConditionResult.FALSE
    assert evaluate_condition(condition, _Item(None)) is ConditionResult.UNKNOWN
    assert (
        evaluate_condition(
            {"gte": {"ref": "claims.is_job.belief_true", "value": 0.8}},
            type("Assessed", (), {"assessments": (_Claim(),)})(),
        )
        is ConditionResult.TRUE
    )
    with pytest.raises(ValueError, match="allowlisted"):
        compile_graph(
            GraphSpec(
                "bad",
                "1",
                (
                    NodeSpec("sanitize", "sanitize", effect="gate"),
                    NodeSpec(
                        "conditional",
                        "source_context",
                        after=("sanitize",),
                        run_if={"eq": {"ref": "metadata.secret", "value": True}},
                    ),
                    NodeSpec("extraction", "extraction", after=("conditional",)),
                    NodeSpec("normalization", "job_normalization", after=("extraction",)),
                    NodeSpec("evidence", "evidence_fanout", after=("normalization",)),
                    NodeSpec(
                        "decision",
                        "decision",
                        after=("evidence",),
                        effect="terminal_decision",
                    ),
                ),
            )
        )


@pytest.mark.asyncio
async def test_false_condition_skips_same_type_stage_and_unknown_runs_by_default() -> None:
    optional = _Optional()
    executor = GraphExecutor(
        _graph(),
        factories={
            "sanitize": _Sanitize(),
            "conditional": optional,
            "extraction": _Sanitize(),
            "normalization": _Sanitize(),
            "evidence": _Sanitize(),
            "decision": _Decision(),
        },
    )
    report = await executor.run(_Item(0.1))
    assert optional.calls == 0
    assert report.node_events["conditional"]["outcome"] == "skipped_condition"
    await executor.run(_Item(None))
    assert optional.calls == 1


def test_conditional_type_changing_stage_is_rejected() -> None:
    with pytest.raises(ValueError, match="preserve payload type"):
        _graph(conditional_node="extraction")
