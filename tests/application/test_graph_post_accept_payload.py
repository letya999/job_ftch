from __future__ import annotations

import asyncio
from dataclasses import dataclass

from job_ftch.application.graph.compiler import compile_graph
from job_ftch.application.graph.contracts import GraphSpec, NodeSpec
from job_ftch.application.graph.executor import GraphExecutor
from job_ftch.domain import (
    AssessedJob,
    DecisionResult,
    JobRecord,
    MatchDecision,
    SourceKind,
    WorkState,
)


@dataclass
class Identity:
    async def process(self, item: object) -> object:
        return item


class Terminal:
    async def process(self, item: JobRecord) -> DecisionResult:
        return DecisionResult(
            assessed_job=AssessedJob(record=item),
            routing_decision=MatchDecision.ACCEPT,
            work_state=WorkState.TERMINAL,
            reasons=("accepted",),
        )


class PostAccept:
    def __init__(self) -> None:
        self.received: JobRecord | None = None

    async def process(self, item: JobRecord) -> JobRecord:
        self.received = item
        return item


def test_post_accept_receives_record_after_typed_terminal_result() -> None:
    graph = compile_graph(
        GraphSpec(
            name="post-accept",
            version="1",
            metadata={"graph_schema": "v2"},
            nodes=(
                NodeSpec("sanitize", "sanitize", effect="gate"),
                NodeSpec("extraction", "extraction", effect="observe", after=("sanitize",)),
                NodeSpec(
                    "normalization",
                    "job_normalization",
                    effect="observe",
                    after=("extraction",),
                ),
                NodeSpec("evidence", "evidence_fanout", effect="observe", after=("normalization",)),
                NodeSpec("decision", "decision", effect="terminal_decision", after=("evidence",)),
                NodeSpec(
                    "post",
                    "post_accept_enrichment",
                    execution="post_accept",
                    effect="side_effect",
                    after=("decision",),
                ),
            ),
        )
    )
    post = PostAccept()
    record = JobRecord(
        raw_item_id="raw-1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="source",
        title="AI engineer",
    )

    report = asyncio.run(
        GraphExecutor(
            graph,
            factories={
                "sanitize": Identity(),
                "extraction": Identity(),
                "normalization": Identity(),
                "evidence": Identity(),
                "decision": Terminal(),
                "post": post,
            },
        ).run(record)
    )

    assert report.status == "ACCEPT"
    assert post.received is record
