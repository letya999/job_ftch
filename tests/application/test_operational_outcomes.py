from __future__ import annotations

import pytest

from job_ftch.application.tenant_store import TenantStore
from job_ftch.config import resolve_outcome_lane_backend
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.sinks.outcome_artifact import CompactRejectedSink, CompactReviewSink
from job_ftch.sinks.outcome_store import StoreOutcomeSink


@pytest.mark.parametrize(
    ("lane", "main", "file_backend", "store"),
    [
        (None, "none", None, False),
        (None, "json_file", "json_file", False),
        ("store", "none", None, True),
        ("both", "none", "json_file", True),
        ("none", "json_file", None, False),
        ("json_file", "none", "json_file", False),
    ],
)
def test_resolve_outcome_lane_backend(
    lane: str | None,
    main: str,
    file_backend: str | None,
    store: bool,
) -> None:
    assert resolve_outcome_lane_backend(lane, main) == (file_backend, store)


@pytest.mark.asyncio
async def test_tenant_store_records_and_lists_outcomes() -> None:
    store = TenantStore("t1", InMemoryStore())
    await store.record_operational_outcome(
        "review",
        {
            "source_run_id": "run-a",
            "stable_id": "s1",
            "title": "A",
            "recorded_at": "2026-01-01T00:00:00+00:00",
        },
    )
    await store.record_operational_outcome(
        "rejected",
        {
            "source_run_id": "run-a",
            "reason": "policy_reject",
            "outcome": "dropped",
            "stable_id": "s2",
            "recorded_at": "2026-01-01T00:00:01+00:00",
        },
    )

    review = await store.list_operational_outcomes("review", run_id="run-a")
    rejected = await store.list_operational_outcomes(
        "rejected", run_id="run-a", reason="policy_reject"
    )
    assert len(review) == 1
    assert review[0]["title"] == "A"
    assert review[0]["tenant_id"] == "t1"
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "policy_reject"


@pytest.mark.asyncio
async def test_store_outcome_sink_via_compact_wrappers() -> None:
    store = TenantStore("t1", InMemoryStore())
    review_sink = CompactReviewSink(StoreOutcomeSink(store, lane="review"))
    rejected_sink = CompactRejectedSink(StoreOutcomeSink(store, lane="rejected"))

    class _Job:
        def model_dump(self, mode: str = "python") -> dict:
            return {
                "stable_id": "job-1",
                "title": "Review me",
                "metadata": {"source_run_id": "run-b"},
                "description_raw": "hello",
            }

    class _Rejected:
        def model_dump(self, mode: str = "python") -> dict:
            return {
                "outcome": "dropped",
                "reason": "policy_reject",
                "details": "x",
                "item_type": "JobRecord",
                "trace": {"source_run_id": "run-b"},
                "snapshot": {"title": "Nope", "description_raw": "body"},
            }

    await review_sink.emit(_Job())
    await rejected_sink.emit(_Rejected())

    reviews = await store.list_operational_outcomes("review")
    rejects = await store.list_operational_outcomes("rejected")
    assert len(reviews) == 1
    assert reviews[0]["title"] == "Review me"
    assert "snapshot" not in rejects[0]
    assert rejects[0]["title"] == "Nope"
