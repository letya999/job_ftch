"""Tests for the dedup settlement coordinator and its integration."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from job_ftch.application.dedup_settlement import (
    DedupSettlementCoordinator,
    SettlementOutcome,
    collect_settlement_participants,
)
from job_ftch.application.graph import GraphCompiler
from job_ftch.application.graph.contracts import GraphSpec, NodeSpec
from job_ftch.application.graph.executor import GraphExecutor
from job_ftch.application.graph.pipeline_stage import GraphPipelineStage


class _Raw:
    def __init__(self, value: str) -> None:
        self.value = value
        self.stable_id = value


class _Candidate:
    def __init__(self, value: str) -> None:
        self.value = value

    def materialize_raw_item(self) -> _Raw:
        return _Raw(self.value)


class _Pass:
    async def process(self, item: _Raw) -> _Raw:
        return item


class _Split:
    is_fan_out_stage = True

    async def process(self, item: _Raw) -> tuple[_Candidate, ...]:
        return _Candidate("a"), _Candidate("b")


class _Terminal:
    async def process(self, item: _Raw) -> dict[str, str]:
        return {"decision": "accept"}


class _DeferResult:
    routing_decision = "accept"
    work_state = "deferred"


class _DeferTerminal:
    async def process(self, item: _Raw) -> _DeferResult:
        return _DeferResult()


class _ClaimProbe:
    """Records commit_claim / release_claim calls."""

    def __init__(self) -> None:
        self.committed: list[str] = []
        self.released: list[str] = []

    async def process(self, item: _Raw) -> _Raw:
        return item

    async def commit_claim(self, item_id: str) -> None:
        self.committed.append(item_id)

    async def release_claim(self, item_id: str) -> None:
        self.released.append(item_id)


class _Explode:
    async def process(self, item: _Raw) -> _Raw:
        raise RuntimeError("downstream failed")


# --- Unit tests for the coordinator ---


def test_settlement_is_idempotent() -> None:
    probe = _ClaimProbe()
    coordinator = DedupSettlementCoordinator((probe,))
    asyncio.run(coordinator.settle("item-1", SettlementOutcome.COMMIT))
    asyncio.run(coordinator.settle("item-1", SettlementOutcome.COMMIT))
    assert probe.committed == ["item-1"]


def test_settlement_commit_and_release() -> None:
    probe = _ClaimProbe()
    coordinator = DedupSettlementCoordinator((probe,))
    asyncio.run(coordinator.settle("accept-1", SettlementOutcome.COMMIT))
    asyncio.run(coordinator.settle("defer-1", SettlementOutcome.RELEASE))
    assert probe.committed == ["accept-1"]
    assert probe.released == ["defer-1"]


def test_missing_participant_warns(capsys: pytest.CaptureFixture[str]) -> None:
    DedupSettlementCoordinator(())
    captured = capsys.readouterr()
    assert "dedup_settlement_no_participants" in captured.out


def test_duplicate_participants_are_deduplicated() -> None:
    probe = _ClaimProbe()
    coordinator = DedupSettlementCoordinator((probe, probe, probe))
    asyncio.run(coordinator.settle("x", SettlementOutcome.COMMIT))
    assert probe.committed == ["x"]


# --- Integration: collect_settlement_participants ---


def test_collect_from_graph_pipeline_stage() -> None:
    graph = GraphCompiler().compile(
        GraphSpec(
            "collect-test",
            "1",
            (
                NodeSpec("sanitize", "sanitize", effect="gate"),
                NodeSpec("dedup", "dedup", effect="gate", after=("sanitize",)),
                NodeSpec("extract", "extraction", after=("dedup",)),
                NodeSpec("normalization", "job_normalization", after=("extract",)),
                NodeSpec(
                    "terminal",
                    "legacy_routing",
                    effect="terminal_decision",
                    after=("normalization",),
                ),
            ),
        )
    )
    probe = _ClaimProbe()
    executor = GraphExecutor(
        graph,
        factories={
            "sanitize": _Pass(),
            "dedup": probe,
            "extract": _Pass(),
            "normalization": _Pass(),
            "terminal": _Terminal(),
        },
    )
    stage = GraphPipelineStage(executor)

    participants = collect_settlement_participants([stage])
    assert len(participants) == 1
    assert participants[0] is probe


def test_collect_from_direct_dedup_settlement() -> None:
    probe = _ClaimProbe()
    participants = collect_settlement_participants([_Pass(), probe, _Pass()])
    assert len(participants) == 1
    assert participants[0] is probe


# --- Integration: executor + coordinator ---


def test_deferred_releases_claim_in_graph_mode() -> None:
    graph = GraphCompiler().compile(
        GraphSpec(
            "deferred-release",
            "1",
            (
                NodeSpec("sanitize", "sanitize", effect="gate"),
                NodeSpec("dedup", "dedup", effect="gate", after=("sanitize",)),
                NodeSpec("extract", "extraction", after=("dedup",)),
                NodeSpec("normalization", "job_normalization", after=("extract",)),
                NodeSpec(
                    "terminal",
                    "legacy_routing",
                    effect="terminal_decision",
                    after=("normalization",),
                ),
            ),
        )
    )
    probe = _ClaimProbe()
    executor = GraphExecutor(
        graph,
        factories={
            "sanitize": _Pass(),
            "dedup": probe,
            "extract": _Pass(),
            "normalization": _Pass(),
            "terminal": _DeferTerminal(),
        },
    )
    item = _Raw("item-1")
    reports = asyncio.run(executor.run_many(item))
    assert reports[0].status == "DEFERRED"

    coordinator = DedupSettlementCoordinator(executor.settlement_participants())
    for report in reports:
        outcome = (
            SettlementOutcome.RELEASE if report.status == "DEFERRED" else SettlementOutcome.COMMIT
        )
        asyncio.run(coordinator.settle(item.stable_id, outcome))

    assert probe.released == ["item-1"]
    assert probe.committed == []


def test_accept_commits_claim() -> None:
    graph = GraphCompiler().compile(
        GraphSpec(
            "accept-commit",
            "1",
            (
                NodeSpec("sanitize", "sanitize", effect="gate"),
                NodeSpec("dedup", "dedup", effect="gate", after=("sanitize",)),
                NodeSpec("extract", "extraction", after=("dedup",)),
                NodeSpec("normalization", "job_normalization", after=("extract",)),
                NodeSpec(
                    "terminal",
                    "legacy_routing",
                    effect="terminal_decision",
                    after=("normalization",),
                ),
            ),
        )
    )
    probe = _ClaimProbe()
    executor = GraphExecutor(
        graph,
        factories={
            "sanitize": _Pass(),
            "dedup": probe,
            "extract": _Pass(),
            "normalization": _Pass(),
            "terminal": _Terminal(),
        },
    )
    item = _Raw("item-1")
    reports = asyncio.run(executor.run_many(item))
    assert reports[0].status == "ACCEPT"

    coordinator = DedupSettlementCoordinator(executor.settlement_participants())
    asyncio.run(coordinator.settle(item.stable_id, SettlementOutcome.COMMIT))

    assert probe.committed == ["item-1"]
    assert probe.released == []


def test_exception_releases_claim_when_caller_settles() -> None:
    graph = GraphCompiler().compile(
        GraphSpec(
            "error-release",
            "1",
            (
                NodeSpec("sanitize", "sanitize", effect="gate"),
                NodeSpec("dedup", "dedup", effect="gate", after=("sanitize",)),
                NodeSpec("explode", "extraction", after=("dedup",)),
                NodeSpec("normalization", "job_normalization", after=("explode",)),
                NodeSpec(
                    "terminal",
                    "legacy_routing",
                    effect="terminal_decision",
                    after=("normalization",),
                ),
            ),
        )
    )
    probe = _ClaimProbe()
    executor = GraphExecutor(
        graph,
        factories={
            "sanitize": _Pass(),
            "dedup": probe,
            "explode": _Explode(),
            "normalization": _Pass(),
            "terminal": _Terminal(),
        },
    )
    coordinator = DedupSettlementCoordinator(executor.settlement_participants())

    item_id = "item-1"
    try:
        asyncio.run(executor.run_many(_Raw(item_id)))
    except RuntimeError:
        asyncio.run(coordinator.settle(item_id, SettlementOutcome.RELEASE))

    assert probe.released == [item_id]
    assert probe.committed == []
