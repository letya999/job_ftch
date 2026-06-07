"""Regression tests for Phase 14 — Cross-source job aggregation."""

import pytest
from pydantic import AnyHttpUrl

from domain import (
    Job,
    SourceKind,
    WorkMode,
)
from infrastructure.stores.job_group_store import InMemoryJobGroupStore
from nodes.aggregation import JobAggregationNode


@pytest.fixture
def store():
    return InMemoryJobGroupStore()


@pytest.fixture
def node(store):
    return JobAggregationNode(store)


@pytest.mark.asyncio
async def test_three_source_merge(node, store):
    """
    Scenario: Same job from career_site, telegram_channel, and telegram_group.
    Expected: 1 JobGroup with source_count=3, canonical_job from career_site.
    """
    common_url = "https://example.com/job1"

    job_cs = Job(
        raw_item_id="item1",
        source_kind=SourceKind.CAREER_SITE,
        source_name="career_portal",
        title="Software Engineer",
        company="TechCorp",
        description="Detailed description from career site.",
        canonical_url=AnyHttpUrl(common_url),
        location="Berlin",
        work_mode=WorkMode.HYBRID,
    )

    job_tg_ch = Job(
        raw_item_id="item2",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="jobs_channel",
        title="Software Engineer",
        company="TechCorp",
        description="Short description.",
        canonical_url=AnyHttpUrl(common_url),
    )

    job_tg_gr = Job(
        raw_item_id="item3",
        source_kind=SourceKind.TELEGRAM_GROUP,
        source_name="jobs_group",
        title="Software Engineer (Berlin)",
        company="TechCorp",
        description="Another description.",
        canonical_url=AnyHttpUrl(common_url),
    )

    # Process in mixed order
    await node.process(job_tg_ch)
    await node.process(job_cs)
    await node.process(job_tg_gr)

    assert await store.count() == 1
    groups = await store.list_groups()
    group = groups[0]

    assert group.source_count == 3
    assert len(group.jobs) == 3
    assert group.canonical_job.source_kind == SourceKind.CAREER_SITE
    assert group.canonical_job.location == "Berlin"
    assert group.canonical_job.work_mode == WorkMode.HYBRID
    assert group.canonical_job.description == "Detailed description from career site."


@pytest.mark.asyncio
async def test_url_matching(node, store):
    """Scenario: Two jobs with same canonical_url from different sources."""
    url = "https://example.com/unique-job"

    job1 = Job(
        raw_item_id="i1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="ch1",
        title="DevOps",
        company="CloudInc",
        description="desc1",
        canonical_url=AnyHttpUrl(url),
    )

    job2 = Job(
        raw_item_id="i2",
        source_kind=SourceKind.TELEGRAM_GROUP,
        source_name="gr1",
        title="DevOps Engineer",
        company="CloudInc",
        description="desc2",
        canonical_url=AnyHttpUrl(url),
    )

    await node.process(job1)
    await node.process(job2)

    assert await store.count() == 1
    assert (await store.list_groups())[0].source_count == 2


@pytest.mark.asyncio
async def test_fingerprint_matching(node, store):
    """
    Scenario: Two jobs with same company_canonical + normalized_title + location but different URLs.
    Expected: Merged into one group.
    """
    job1 = Job(
        raw_item_id="i1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="ch1",
        title="Python Developer",
        company="PySoft",
        company_canonical="pysoft",
        location="Remote",
        description="desc1",
        canonical_url=AnyHttpUrl("https://pysoft.com/j1"),
    )

    job2 = Job(
        raw_item_id="i2",
        source_kind=SourceKind.TELEGRAM_GROUP,
        source_name="gr1",
        title="PYTHON developer",  # Case and extra space
        company="PySoft Inc.",
        company_canonical="pysoft",
        location=" remote ",  # extra spaces
        description="desc2",
        canonical_url=AnyHttpUrl("https://pysoft.com/j2"),
    )

    await node.process(job1)
    await node.process(job2)

    assert await store.count() == 1
    assert (await store.list_groups())[0].source_count == 2


@pytest.mark.asyncio
async def test_fuzzy_title_matching(node, store):
    """Scenario: title 'ML Engineer, Sber' vs 'ML Engineer at Sber'."""
    job1 = Job(
        raw_item_id="i1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="ch1",
        title="ML Engineer, Sber",
        company="Sber",
        description="desc1",
    )

    job2 = Job(
        raw_item_id="i2",
        source_kind=SourceKind.TELEGRAM_GROUP,
        source_name="gr1",
        title="ML Engineer at Sber",
        company="Sber",
        description="desc2",
    )

    await node.process(job1)
    await node.process(job2)

    assert await store.count() == 1
    assert (await store.list_groups())[0].source_count == 2


@pytest.mark.asyncio
async def test_no_false_merge(node, store):
    """Scenario: Two genuinely different jobs (different company + title)."""
    job1 = Job(
        raw_item_id="i1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="ch1",
        title="Backend dev",
        company="Company A",
        description="desc1",
    )

    job2 = Job(
        raw_item_id="i2",
        source_kind=SourceKind.TELEGRAM_GROUP,
        source_name="gr1",
        title="Frontend dev",
        company="Company B",
        description="desc2",
    )

    await node.process(job1)
    await node.process(job2)

    assert await store.count() == 2


@pytest.mark.asyncio
async def test_stats_tracking(node, store):
    """Verify that JobGroupStore tracks stats correctly."""
    job1 = Job(
        raw_item_id="i1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="ch1",
        title="Job 1",
        company="Comp 1",
        description="desc1",
    )

    job2 = Job(
        raw_item_id="i2",
        source_kind=SourceKind.TELEGRAM_GROUP,
        source_name="gr1",
        title="Job 1",
        company="Comp 1",
        description="desc2",
    )

    job3 = Job(
        raw_item_id="i3",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="ch1",
        title="Job 3",
        company="Comp 3",
        description="desc3",
    )

    await node.process(job1)  # New group
    await node.process(job2)  # Merge
    await node.process(job3)  # New group

    assert store.new_groups_created == 2
    assert store.merged_into_group == 1
    assert store.by_source_kind_new[str(SourceKind.TELEGRAM_CHANNEL)] == 2
    assert store.by_source_kind_merged[str(SourceKind.TELEGRAM_GROUP)] == 1
