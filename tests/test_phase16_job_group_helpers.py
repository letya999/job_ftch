from datetime import UTC, datetime

import pytest

from domain import Job, SourceKind
from domain.job_group import (
    create_job_group,
    merge_job_into_group,
    remove_job_from_group,
)


@pytest.fixture
def sample_job():
    return Job(
        raw_item_id="1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="chan",
        title="Python Dev",
        company="Acme",
        description="Great job",
        metadata={"fetched_at": datetime.now(UTC)},
    )


def test_create_job_group(sample_job):
    group = create_job_group(sample_job)
    assert group.source_count == 1
    assert group.canonical_job.title == "Python Dev"
    assert len(group.source_attributions) == 1


def test_merge_job_into_group(sample_job):
    group = create_job_group(sample_job)

    # Merge new job from different source
    job2 = sample_job.model_copy(
        update={
            "raw_item_id": "2",
            "source_kind": SourceKind.CAREER_SITE,
            "title": "Senior Python Dev",
        }
    )

    merged = merge_job_into_group(group, job2)
    assert merged.source_count == 2
    # CAREER_SITE takes priority over TELEGRAM_CHANNEL
    assert merged.canonical_job.title == "Senior Python Dev"
    assert len(merged.source_attributions) == 2


def test_remove_job_from_group(sample_job):
    group = create_job_group(sample_job)

    # Remove the only job
    res = remove_job_from_group(group, "1")
    assert res is None

    # Remove non-existent job
    res2 = remove_job_from_group(group, "xyz")
    assert res2 is not None
    assert res2.source_count == 1
