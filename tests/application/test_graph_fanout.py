from __future__ import annotations

import asyncio

from job_ftch.application.graph import GraphCompiler
from job_ftch.application.graph.contracts import GraphSpec, NodeSpec
from job_ftch.application.graph.executor import GraphExecutor


class Raw:
    def __init__(self, value: str) -> None:
        self.value = value
        self.stable_id = value


class Candidate:
    def __init__(self, value: str) -> None:
        self.value = value

    def materialize_raw_item(self) -> Raw:
        return Raw(self.value)


class Pass:
    async def process(self, item: Raw) -> Raw:
        return item


class Split:
    is_fan_out_stage = True

    async def process(self, item: Raw) -> tuple[Candidate, ...]:
        return Candidate("a"), Candidate("b")


class Gate:
    async def process(self, item: Raw) -> Raw | None:
        return item if item.value == "a" else None


class Terminal:
    async def process(self, item: Raw) -> dict[str, str]:
        return {"decision": "accept"}


class ClaimProbe:
    def __init__(self) -> None:
        self.committed: list[str] = []
        self.released: list[str] = []

    async def process(self, item: Raw) -> Raw:
        return item

    async def commit_claim(self, item_id: str) -> None:
        self.committed.append(item_id)

    async def release_claim(self, item_id: str) -> None:
        self.released.append(item_id)


class Explode:
    async def process(self, item: Raw) -> Raw:
        raise RuntimeError("downstream failed")


def test_run_many_materializes_fanout_candidates_and_keeps_independent_outcomes() -> None:
    graph = GraphCompiler().compile(
        GraphSpec(
            "fanout",
            "1",
            (
                NodeSpec("sanitize", "sanitize", effect="gate"),
                NodeSpec("split", "candidate_segmentation", after=("sanitize",)),
                NodeSpec("gate", "garbage_filter", after=("split",), effect="gate"),
                NodeSpec("extract", "extraction", after=("gate",)),
                NodeSpec("normalization", "job_normalization", after=("extract",)),
                NodeSpec(
                    "terminal",
                    "legacy_routing",
                    after=("normalization",),
                    effect="terminal_decision",
                ),
            ),
        )
    )
    executor = GraphExecutor(
        graph,
        factories={
            "sanitize": Pass(),
            "split": Split(),
            "gate": Gate(),
            "extract": Pass(),
            "normalization": Pass(),
            "terminal": Terminal(),
        },
    )
    reports = asyncio.run(executor.run_many(Raw("source")))
    assert [report.status for report in reports] == ["ACCEPT", "REJECT"]


def test_run_many_commits_deferred_dedup_claim_after_terminal_outcome() -> None:
    graph = GraphCompiler().compile(
        GraphSpec(
            "claims",
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
    claim = ClaimProbe()
    executor = GraphExecutor(
        graph,
        factories={
            "sanitize": Pass(),
            "dedup": claim,
            "extract": Pass(),
            "normalization": Pass(),
            "terminal": Terminal(),
        },
    )

    asyncio.run(executor.run_many(Raw("claim-1")))

    assert claim.committed == ["claim-1"]
    assert claim.released == []


def test_run_many_releases_deferred_dedup_claim_on_graph_failure() -> None:
    graph = GraphCompiler().compile(
        GraphSpec(
            "claims-failure",
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
    claim = ClaimProbe()
    executor = GraphExecutor(
        graph,
        factories={
            "sanitize": Pass(),
            "dedup": claim,
            "explode": Explode(),
            "normalization": Pass(),
            "terminal": Terminal(),
        },
    )

    try:
        asyncio.run(executor.run_many(Raw("claim-2")))
    except RuntimeError as exc:
        assert str(exc) == "downstream failed"
    else:
        raise AssertionError("graph failure must propagate")

    assert claim.committed == []
    assert claim.released == ["claim-2"]


def test_graph_fanout_settles_child_dedup_claims() -> None:
    graph = GraphCompiler().compile(
        GraphSpec(
            "fanout-claims",
            "1",
            (
                NodeSpec("sanitize", "sanitize", effect="gate"),
                NodeSpec("split", "candidate_segmentation", after=("sanitize",)),
                NodeSpec("dedup", "dedup", effect="gate", after=("split",)),
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
    claim = ClaimProbe()
    executor = GraphExecutor(
        graph,
        factories={
            "sanitize": Pass(),
            "split": Split(),
            "dedup": claim,
            "extract": Pass(),
            "normalization": Pass(),
            "terminal": Terminal(),
        },
    )

    asyncio.run(executor.run_many(Raw("parent-1")))

    # The Split node returns Candidate("a") and Candidate("b").
    # `materialize_raw_item` makes them into Raw("a") and Raw("b") in our test stub.
    # So both "a" and "b" should pass through dedup and have their claims committed.
    # Plus, the `parent-1` also entered the graph. But `DedupNode` runs AFTER split, so only "a" and "b" get claims.
    # But wait, our `executor` tracks ALL items that entered `_run_from`. So it will call `commit_claim("parent-1")`,
    # `commit_claim("a")`, and `commit_claim("b")`!
    assert sorted(claim.committed) == ["a", "b", "parent-1"]
    assert claim.released == []


def test_graph_fanout_releases_child_dedup_claims_on_failure() -> None:
    graph = GraphCompiler().compile(
        GraphSpec(
            "fanout-claims-failure",
            "1",
            (
                NodeSpec("sanitize", "sanitize", effect="gate"),
                NodeSpec("split", "candidate_segmentation", after=("sanitize",)),
                NodeSpec("dedup", "dedup", effect="gate", after=("split",)),
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
    claim = ClaimProbe()
    executor = GraphExecutor(
        graph,
        factories={
            "sanitize": Pass(),
            "split": Split(),
            "dedup": claim,
            "explode": Explode(),
            "normalization": Pass(),
            "terminal": Terminal(),
        },
    )

    try:
        asyncio.run(executor.run_many(Raw("parent-1")))
    except RuntimeError as exc:
        assert str(exc) == "downstream failed"
    else:
        raise AssertionError("graph failure must propagate")

    assert claim.committed == []
    assert sorted(claim.released) == ["a", "parent-1"]
