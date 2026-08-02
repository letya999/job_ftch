"""Drops must name the node that made them.

A run drained by dedup and a run whose candidates were rejected as non-vacancies
used to produce the identical ``node_returned_none`` bucket, and the bot rendered
both as "не-вакансии". That made a dirty-database run indistinguishable from a
broken selection filter.
"""

from pathlib import Path

import pytest

from job_ftch.adapters.telegram_bot.handlers.pipeline import _split_drop_buckets
from job_ftch.application.pipeline import Pipeline, _drop_reason
from job_ftch.application.run_report import split_drop_buckets
from job_ftch.domain.models import RawItem, SourceKind
from job_ftch.nodes.sanitize import SanitizeNode
from job_ftch.sinks.json_file import JsonFileSink
from tests.test_pipeline import InMemoryStore, StubSource


class AlreadySeenNode:
    """Stand-in for the graph dedup node: silently drops everything."""

    async def process(self, item: object) -> object | None:
        return None


@pytest.mark.unit
def test_drop_reason_names_the_dropping_stage() -> None:
    assert _drop_reason({"drop_stage": "dedup"}) == "node_returned_none:dedup"
    assert _drop_reason({"drop_stage": "garbage"}) == "node_returned_none:garbage"


@pytest.mark.unit
def test_drop_reason_falls_back_when_stage_is_unknown() -> None:
    assert _drop_reason({}) == "node_returned_none"
    assert _drop_reason({"drop_stage": "  "}) == "node_returned_none"


@pytest.mark.unit
def test_drop_reason_prefers_an_explicit_reason() -> None:
    """Budget drops already carry a precise reason that must not be overwritten."""
    result = {"drop_stage": "pipeline", "drop_reason": "budget_exhausted"}
    assert _drop_reason(result) == "budget_exhausted"


@pytest.mark.asyncio
async def test_pipeline_attributes_a_node_drop_to_that_node(tmp_path: Path) -> None:
    items = [
        RawItem(
            source_kind=SourceKind.DEBUG, source_name="debug", external_id=str(i), text=f"job {i}"
        )
        for i in range(3)
    ]
    pipeline = Pipeline(
        source=StubSource(items),
        sanitize_node=SanitizeNode(),
        nodes=[AlreadySeenNode()],
        sink=JsonFileSink(tmp_path / "out.json"),
        store=InMemoryStore(),
    )

    summary = await pipeline.run()

    assert summary.dropped == 3
    assert summary.drop_reasons == {"node_returned_none:AlreadySeenNode": 3}


@pytest.mark.unit
def test_split_drop_buckets_separates_seen_from_non_vacancy() -> None:
    already_seen, non_vacancy, other = _split_drop_buckets(
        {
            "node_returned_none:dedup": 207,
            "node_returned_none:SnapshotFilterNode": 3,
            "already_processed": 5,
            "node_returned_none:garbage": 11,
            "node_returned_none:post_type": 2,
            "max_items_budget": 4,
        }
    )
    assert already_seen == 215
    assert non_vacancy == 13
    assert other == 4


@pytest.mark.unit
def test_split_drop_buckets_counts_raw_seen_and_duplicate_reasons_as_seen() -> None:
    already_seen, non_vacancy, other = _split_drop_buckets(
        {
            "already_seen": 682,
            "duplicate_content": 909,
            "duplicate_url": 2,
            "duplicate_near_match": 1,
            "low_relevance_prefilter": 21,
        }
    )
    assert already_seen == 1594
    assert non_vacancy == 0
    assert other == 21


@pytest.mark.unit
def test_runtime_drop_buckets_separate_low_relevance_from_other_drops() -> None:
    buckets = split_drop_buckets(
        {
            "already_seen": 10,
            "node_returned_none:garbage": 3,
            "low_relevance_prefilter": 2,
            "source_hard_deadline_exceeded": 1,
        }
    )

    assert buckets.already_seen == 10
    assert buckets.non_vacancy == 3
    assert buckets.low_relevance == 2
    assert buckets.other == 1


@pytest.mark.unit
def test_split_drop_buckets_ignores_empty_counters() -> None:
    assert _split_drop_buckets({}) == (0, 0, 0)
    assert _split_drop_buckets({"node_returned_none:dedup": 0}) == (0, 0, 0)


@pytest.mark.unit
def test_split_drop_buckets_treats_an_unnamed_drop_as_other() -> None:
    """Legacy unattributed drops must not be misreported as non-vacancies."""
    assert _split_drop_buckets({"node_returned_none": 9}) == (0, 0, 9)
