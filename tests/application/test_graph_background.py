from __future__ import annotations

import asyncio

from job_ftch.application.graph import GraphCompiler
from job_ftch.application.graph.contracts import EvidencePatch, GraphSpec, NodeManifest, NodeSpec
from job_ftch.application.graph.executor import GraphExecutor
from job_ftch.application.graph.registry import register


class Payload:
    def __init__(self, value: str, evidence: str = "") -> None:
        self.value, self.evidence = value, evidence


class Producer:
    async def process(self, item: Payload) -> Payload:
        return Payload(item.value, "bge")


class Identity:
    async def process(self, item: Payload) -> Payload:
        return item


class Consumer:
    async def process(self, item: Payload) -> dict[str, str]:
        return {"decision": "accept" if item.evidence == "bge" else "reject"}


class PatchProducer:
    async def process(self, item: Payload) -> EvidencePatch:
        return EvidencePatch(
            claim="profile_relevance",
            producer="test_bge",
            independence_group="semantic_bge",
            features={"dense_margin": 0.2},
            recommendation="support",
        )


register(
    NodeManifest(
        node_id="test_background_route",
        factory="TestBackgroundRoute",
        input_type="RawItem",
        output_type="RawItem",
        capabilities=("terminal_decision",),
        allowed_execution=("sequential",),
        allowed_effects=("terminal_decision",),
        terminal_eligible=True,
    )
)


def test_background_payload_is_joined_before_sequential_consumer() -> None:
    graph = GraphCompiler().compile(
        GraphSpec(
            "background",
            "1",
            (
                NodeSpec("sanitize", "sanitize", effect="gate"),
                NodeSpec(
                    "bge",
                    "bgem3_embed",
                    execution="background",
                    after=("sanitize",),
                    join_at="barrier",
                ),
                NodeSpec(
                    "route",
                    "test_background_route",
                    effect="terminal_decision",
                    after=("sanitize",),
                ),
            ),
            metadata={"barriers": ["barrier"]},
        )
    )
    report = asyncio.run(
        GraphExecutor(
            graph, factories={"sanitize": Producer(), "bge": Producer(), "route": Consumer()}
        ).run(Payload("x"))
    )
    assert report.status == "ACCEPT"
    assert report.node_events["bge"]["execution"] == "background"
    assert report.node_events["bge"]["outcome"] == "pass"
    assert report.node_events["route"]["terminal_status"] == "ACCEPT"


def test_parallel_evidence_patch_is_retained_without_payload_replacement() -> None:
    graph = GraphCompiler().compile(
        GraphSpec(
            "patch",
            "1",
            (
                NodeSpec("sanitize", "sanitize", effect="gate"),
                NodeSpec(
                    "bge",
                    "bgem3_embed",
                    execution="background",
                    after=("sanitize",),
                    join_at="barrier",
                ),
                NodeSpec(
                    "route",
                    "test_background_route",
                    effect="terminal_decision",
                    after=("sanitize",),
                ),
            ),
            metadata={"barriers": ["barrier"]},
        )
    )
    report = asyncio.run(
        GraphExecutor(
            graph,
            factories={"sanitize": Identity(), "bge": PatchProducer(), "route": Consumer()},
        ).run(Payload("x"))
    )
    assert report.status == "REJECT"
    assert report.evidence.patches[0].features["dense_margin"] == 0.2
    assert report.node_events["bge"]["evidence_produced"][0]["claim"] == "profile_relevance"
