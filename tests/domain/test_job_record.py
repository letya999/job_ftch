import pytest

from job_ftch.domain import CompensationRange, JobRecord, SourceKind, WorkMode


@pytest.mark.unit
def test_job_record_round_trip_serialization():
    record = JobRecord(
        raw_item_id="r1",
        source_kind=SourceKind.DEBUG,
        source_name="debug",
        title="Senior ML Engineer",
        company="OpenAI",
        description="Build large-scale ML systems for production use.",
        work_mode=WorkMode.REMOTE,
        relevance_score=0.7,
        quality_score=0.8,
    )
    dump = record.model_dump()
    validated = JobRecord.model_validate(dump)
    assert validated == record


@pytest.mark.unit
def test_job_record_defaults_work_mode_unknown():
    record = JobRecord(
        raw_item_id="r1",
        source_kind=SourceKind.DEBUG,
        source_name="debug",
        title="Senior ML Engineer",
        company="OpenAI",
        description="test",
        relevance_score=0.7,
        quality_score=0.8,
    )
    assert record.work_mode == WorkMode.UNKNOWN


@pytest.mark.unit
def test_job_record_profile_scores_immutable_tuple():
    record = JobRecord(
        raw_item_id="r1",
        source_kind=SourceKind.DEBUG,
        source_name="debug",
        title="title",
        company="company",
        description="desc",
        profile_scores=(),
    )
    assert isinstance(record.profile_scores, tuple)


@pytest.mark.unit
def test_job_record_model_copy_preserves_all_fields():
    record = JobRecord(
        raw_item_id="r1",
        source_kind=SourceKind.DEBUG,
        source_name="debug",
        title="title",
        company="company",
        description="desc",
        relevance_score=0.7,
        quality_score=0.8,
    )
    copied = record.model_copy()
    assert copied == record


@pytest.mark.unit
def test_job_record_compensation_range_validates_min_max():
    with pytest.raises(ValueError, match="Compensation min_amount must be <= max_amount"):
        CompensationRange(min_amount=1000, max_amount=500, currency="USD")
