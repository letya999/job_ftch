"""Comprehensive unit and integration tests for Phase 14 — Cross-source job aggregation."""

import pytest
from pydantic import AnyHttpUrl

from job_ftch.application.identity import JobIdentityMatcher
from job_ftch.domain import (
    JobRecord,
    SourceKind,
    WorkMode,
)
from job_ftch.domain.job_group import compute_identity_fingerprint, merge_jobs
from job_ftch.infrastructure.stores.job_group_store import InMemoryJobGroupStore
from job_ftch.nodes.aggregation import JobAggregationNode


def _record(**overrides: object) -> JobRecord:
    payload: dict[str, object] = {
        "raw_item_id": "1",
        "source_kind": SourceKind.DEBUG,
        "source_name": "debug",
        "title": "A",
        "company": "B",
        "description": "desc",
    }
    payload.update(overrides)
    return JobRecord.model_validate(payload)

# --- Unit Tests: compute_identity_fingerprint ---


def test_fingerprint_normalization() -> None:
    """Test that title is normalized (lowercase, punctuation stripped, sorted words)."""
    job1 = _record(
        raw_item_id="1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="ch1",
        title="Senior Python Developer, Backend!",
        company="TechCorp",
        description="desc",
    )
    job2 = _record(
        raw_item_id="2",
        source_kind=SourceKind.TELEGRAM_GROUP,
        source_name="gr1",
        title="backend developer python senior",
        company="TechCorp",
        description="desc",
    )
    assert compute_identity_fingerprint(job1) == compute_identity_fingerprint(job2)


def test_fingerprint_company_fallback() -> None:
    """Test that company is used if company_canonical is missing."""
    job1 = _record(
        raw_item_id="1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="ch1",
        title="Dev",
        company="TechCorp",
        company_canonical="techcorp",
        description="desc",
    )
    job2 = _record(
        raw_item_id="2",
        source_kind=SourceKind.TELEGRAM_GROUP,
        source_name="gr1",
        title="Dev",
        company="TechCorp ",  # trailing space
        description="desc",
    )
    assert compute_identity_fingerprint(job1) == compute_identity_fingerprint(job2)


def test_fingerprint_location() -> None:
    """Test that location is case-insensitive and stripped."""
    job1 = _record(
        raw_item_id="1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="ch1",
        title="Dev",
        company="TechCorp",
        location=" New York ",
        description="desc",
    )
    job2 = _record(
        raw_item_id="2",
        source_kind=SourceKind.TELEGRAM_GROUP,
        source_name="gr1",
        title="Dev",
        company="TechCorp",
        location="new york",
        description="desc",
    )
    assert compute_identity_fingerprint(job1) == compute_identity_fingerprint(job2)


# --- Unit Tests: merge_jobs ---


def test_merge_jobs_priority() -> None:
    """Test that highest priority source (CAREER_SITE) wins most fields."""
    job_cs = _record(
        raw_item_id="1",
        source_kind=SourceKind.CAREER_SITE,
        source_name="cs",
        title="SE",
        company="Corp",
        location="Berlin",
        work_mode=WorkMode.HYBRID,
        description="Short description",
        metadata={"key": "cs_val"},
    )
    job_tg = _record(
        raw_item_id="2",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="tg",
        title="Software Engineer",
        company="Corp LLC",
        location="Germany",
        work_mode=WorkMode.REMOTE,
        description="A very long description that should win because it is longer.",
        metadata={"key": "tg_val", "other": "val"},
    )

    merged = merge_jobs([job_tg, job_cs])

    assert merged.source_kind == SourceKind.CAREER_SITE
    assert merged.title == "SE"
    assert merged.company == "Corp"
    assert merged.location == "Berlin"
    assert merged.work_mode == WorkMode.HYBRID
    # Longest description wins
    assert merged.description == "A very long description that should win because it is longer."
    # Metadata higher priority overwrites
    assert merged.metadata["key"] == "cs_val"
    assert merged.metadata["other"] == "val"


def test_merge_jobs_max_scores() -> None:
    """Test that quality_score and relevance_score take the maximum."""
    job1 = _record(
        raw_item_id="1",
        source_kind=SourceKind.DEBUG,
        source_name="d1",
        title="A",
        company="B",
        description="C",
        quality_score=0.5,
        relevance_score=0.2,
    )
    job2 = _record(
        raw_item_id="2",
        source_kind=SourceKind.DEBUG,
        source_name="d2",
        title="A",
        company="B",
        description="C",
        quality_score=0.8,
        relevance_score=0.1,
    )

    merged = merge_jobs([job1, job2])
    assert merged.quality_score == 0.8
    assert merged.relevance_score == 0.2


def test_merge_jobs_empty() -> None:
    with pytest.raises(ValueError, match="Cannot merge empty list"):
        merge_jobs([])


# --- Unit Tests: JobIdentityMatcher ---


@pytest.mark.asyncio
async def test_matcher_url_exact() -> None:
    store = InMemoryJobGroupStore()
    matcher = JobIdentityMatcher(store)

    job = _record(
        raw_item_id="1",
        source_kind=SourceKind.DEBUG,
        source_name="d",
        title="A",
        company="B",
        description="C",
        canonical_url=AnyHttpUrl("https://example.com/1"),
    )
    await store.create(job)

    group_id = await matcher.find_matching_group(job)
    assert group_id is not None

    job_diff_url = _record(
        raw_item_id="2",
        source_kind=SourceKind.DEBUG,
        source_name="d",
        title="Different Title",
        company="B",
        description="C",
        canonical_url=AnyHttpUrl("https://example.com/2"),
    )
    assert await matcher.find_matching_group(job_diff_url) is None


# --- Integration Tests: InMemoryJobGroupStore ---


@pytest.mark.asyncio
async def test_store_merge_existing_source() -> None:
    """Merge job from the same source updates the job in the group instead of appending."""
    store = InMemoryJobGroupStore()
    job1 = _record(
        raw_item_id="1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="ch1",
        title="A",
        company="B",
        description="Desc 1",
    )
    group = await store.create(job1)

    job1_updated = _record(
        raw_item_id="2",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="ch1",
        title="A",
        company="B",
        description="Desc 2",
    )
    updated_group = await store.merge(group.group_id, job1_updated)

    assert updated_group.source_count == 1
    assert updated_group.canonical_job.description == "Desc 2"


@pytest.mark.asyncio
async def test_store_merge_new_source() -> None:
    """Merge job from a different source appends to the group."""
    store = InMemoryJobGroupStore()
    job1 = _record(
        raw_item_id="1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="ch1",
        title="A",
        company="B",
        description="Desc 1",
    )
    group = await store.create(job1)

    job2 = _record(
        raw_item_id="2",
        source_kind=SourceKind.TELEGRAM_GROUP,
        source_name="gr1",
        title="A",
        company="B",
        description="Desc 2 is much longer",
    )
    updated_group = await store.merge(group.group_id, job2)

    assert updated_group.source_count == 2
    assert updated_group.canonical_job.description == "Desc 2 is much longer"


# --- Integration Tests: JobAggregationNode ---


@pytest.mark.asyncio
async def test_node_fuzzy_match() -> None:
    """Verify fuzzy title matching works when enabled."""
    store = InMemoryJobGroupStore()
    node = JobAggregationNode(store, fuzzy_title_threshold=80.0, enable_fuzzy=True)

    job1 = _record(
        raw_item_id="1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="ch1",
        title="Backend Engineer at OpenAI",
        company="OpenAI",
        description="desc",
    )
    job2 = _record(
        raw_item_id="2",
        source_kind=SourceKind.TELEGRAM_GROUP,
        source_name="gr1",
        title="Backend Engineer, OpenAI",
        company="OpenAI",
        description="desc",
    )

    await node.process(job1)
    await node.process(job2)

    assert await store.count() == 1


@pytest.mark.asyncio
async def test_node_fuzzy_match_disabled() -> None:
    """Verify fuzzy title matching is skipped when disabled."""
    store = InMemoryJobGroupStore()
    node = JobAggregationNode(store, enable_fuzzy=False)

    job1 = _record(
        raw_item_id="1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="ch1",
        title="Backend Engineer at OpenAI",
        company="OpenAI",
        description="desc",
    )
    job2 = _record(
        raw_item_id="2",
        source_kind=SourceKind.TELEGRAM_GROUP,
        source_name="gr1",
        title="Backend Engineer, OpenAI",
        company="OpenAI",
        description="desc",
    )

    await node.process(job1)
    await node.process(job2)

    assert await store.count() == 2


@pytest.mark.asyncio
async def test_node_fuzzy_threshold() -> None:
    """Verify jobs below the fuzzy threshold are not merged."""
    store = InMemoryJobGroupStore()
    node = JobAggregationNode(store, fuzzy_title_threshold=95.0, enable_fuzzy=True)

    job1 = _record(
        raw_item_id="1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="ch1",
        title="Backend Engineer at OpenAI",
        company="OpenAI",
        description="desc",
    )
    job2 = _record(
        raw_item_id="2",
        source_kind=SourceKind.TELEGRAM_GROUP,
        source_name="gr1",
        title="Software Engineer, OpenAI",
        company="OpenAI",
        description="desc",
    )

    await node.process(job1)
    await node.process(job2)

    # Threshold is high, these shouldn't match fuzzily
    assert await store.count() == 2


@pytest.mark.asyncio
async def test_node_passes_job_through() -> None:
    """Verify that JobAggregationNode returns the job unmodified."""
    store = InMemoryJobGroupStore()
    node = JobAggregationNode(store)

    job = _record(
        raw_item_id="1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="ch1",
        title="A",
        company="B",
        description="C",
    )
    returned_job = await node.process(job)

    assert returned_job is job


@pytest.mark.asyncio
async def test_stats_tracking_comprehensive() -> None:
    """Verify complete stats tracking across multiple merges and creates."""
    store = InMemoryJobGroupStore()
    node = JobAggregationNode(store)

    job1 = _record(
        raw_item_id="1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="ch1",
        title="Job A",
        company="Corp",
        description="desc",
    )
    job2 = _record(
        raw_item_id="2",
        source_kind=SourceKind.TELEGRAM_GROUP,
        source_name="gr1",
        title="Job A",
        company="Corp",
        description="desc",
    )
    job3 = _record(
        raw_item_id="3",
        source_kind=SourceKind.CAREER_SITE,
        source_name="cs",
        title="Job A",
        company="Corp",
        description="desc",
    )
    job4 = _record(
        raw_item_id="4",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="ch1",
        title="Job B",
        company="Inc",
        description="desc",
    )

    await node.process(job1)  # Create group 1
    await node.process(job2)  # Merge into 1
    await node.process(job3)  # Merge into 1
    await node.process(job4)  # Create group 2

    assert store.new_groups_created == 2
    assert store.merged_into_group == 2
    assert store.by_source_kind_new[str(SourceKind.TELEGRAM_CHANNEL)] == 2
    assert store.by_source_kind_merged[str(SourceKind.TELEGRAM_GROUP)] == 1
    assert store.by_source_kind_merged[str(SourceKind.CAREER_SITE)] == 1
