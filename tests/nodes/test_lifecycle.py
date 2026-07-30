from __future__ import annotations

from datetime import UTC, datetime

import pytest

from job_ftch.domain import JobStatus
from job_ftch.nodes.lifecycle import JobLifecycleNode


@pytest.mark.anyio
async def test_lifecycle_records_observation_as_freshness_evidence(make_job_record) -> None:
    job = make_job_record(fetched_at=datetime.now(UTC))

    result = await JobLifecycleNode().process(job)

    assert result.metadata["freshness_evidence_state"] == "observed_at"


@pytest.mark.anyio
async def test_lifecycle_records_explicit_closed_status(make_job_record) -> None:
    job = make_job_record(metadata={"status": "closed"})

    result = await JobLifecycleNode().process(job)

    assert result.status is JobStatus.FILLED
    assert result.metadata["freshness_evidence_state"] == "explicit_status"
