from __future__ import annotations

import pytest

from job_ftch.application.pipeline import Pipeline
from job_ftch.domain import RawItem, SourceKind
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.nodes.candidate_segmentation import CandidateSegmentationNode
from job_ftch.nodes.sanitize import SanitizeNode


class _Source:
    def __init__(self, item: RawItem) -> None:
        self._item = item

    def fetch(self):  # type: ignore[no-untyped-def]
        async def _items():  # type: ignore[no-untyped-def]
            yield self._item

        return _items()


class _Sink:
    def __init__(self) -> None:
        self.items: list[RawItem] = []

    async def emit(self, item: RawItem) -> None:
        self.items.append(item)


@pytest.mark.asyncio
async def test_pipeline_expands_declared_segments_independently() -> None:
    parent = RawItem(
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="jobs",
        external_id="42",
        text="digest",
        metadata={
            "candidate_segments": [
                {"text": "Hiring Backend Engineer at Acme"},
                {"text": "Vacancy: Data Analyst at Beta"},
            ]
        },
    )
    sink = _Sink()
    pipeline = Pipeline(
        source=_Source(parent),
        sanitize_node=SanitizeNode(),
        nodes=[CandidateSegmentationNode()],
        sink=sink,
        store=InMemoryStore(),
    )

    summary = await pipeline.run()

    assert summary.fetched == 1
    assert summary.emitted == 2
    assert [item.text for item in sink.items] == [
        "Hiring Backend Engineer at Acme",
        "Vacancy: Data Analyst at Beta",
    ]
    assert sink.items[0].metadata["parent_observation_id"] == parent.stable_id


@pytest.mark.asyncio
async def test_failed_span_does_not_cancel_its_sibling() -> None:
    class _FailOne:
        async def process(self, item: RawItem) -> RawItem:
            if "Beta" in item.text:
                raise RuntimeError("bad segment")
            return item

    parent = RawItem(
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="jobs",
        external_id="43",
        text="digest",
        metadata={"candidate_segments": ["Hiring at Acme", "Vacancy at Beta"]},
    )
    sink = _Sink()
    pipeline = Pipeline(
        source=_Source(parent),
        sanitize_node=SanitizeNode(),
        nodes=[CandidateSegmentationNode(), _FailOne()],
        sink=sink,
        store=InMemoryStore(),
    )

    summary = await pipeline.run()

    assert [item.text for item in sink.items] == ["Hiring at Acme"]
    assert summary.failed == 1
