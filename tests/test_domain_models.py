from __future__ import annotations

from pydantic import ValidationError

from job_ftch.domain import (
    CompensationRange,
    Job,
    JobExtractionStatus,
    RawItem,
    SourceKind,
    WorkMode,
)


def test_raw_item_valid_payload_has_stable_id() -> None:
    item = RawItem(
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="  ai-jobs  ",
        external_id=" 42 ",
        url="https://t.me/ai_jobs/42",
        text=" Senior AI engineer role ",
        metadata={"lang": "en"},
    )

    dumped = item.model_dump(mode="json")

    assert item.source_name == "ai-jobs"
    assert item.external_id == "42"
    assert item.text == "Senior AI engineer role"
    assert dumped["stable_id"] == item.stable_id


def test_raw_item_rejects_blank_text() -> None:
    try:
        RawItem(
            source_kind=SourceKind.DEBUG,
            source_name="debug",
            external_id="1",
            text="   ",
        )
    except ValidationError as exc:
        assert "text must not be blank" in str(exc)
    else:
        raise AssertionError("Expected RawItem validation to fail.")


def test_raw_item_requires_external_id_or_url() -> None:
    try:
        RawItem(
            source_kind=SourceKind.DEBUG,
            source_name="debug",
            text="valid",
        )
    except ValidationError as exc:
        assert "requires external_id or url" in str(exc)
    else:
        raise AssertionError("Expected RawItem validation to fail.")


def test_job_valid_payload_has_serializable_output() -> None:
    job = Job(
        raw_item_id="raw-1",
        source_kind=SourceKind.CAREER_SITE,
        source_name="company-careers",
        title=" Senior ML Engineer ",
        company=" Example Corp ",
        description=" Build applied AI systems. ",
        canonical_url="https://company.example/jobs/123",
        work_mode=WorkMode.REMOTE,
        compensation=CompensationRange(currency="USD", min_amount=120000, max_amount=180000),
    )

    dumped = job.model_dump(mode="json")

    assert job.title == "Senior ML Engineer"
    assert job.company == "Example Corp"
    assert dumped["stable_id"] == job.stable_id
    assert dumped["compensation"]["currency"] == "USD"


def test_job_allows_partial_extraction_payload() -> None:
    job = Job(
        raw_item_id="raw-2",
        source_kind=SourceKind.TELEGRAM_GROUP,
        source_name="ai-hiring",
        title="Founding AI engineer",
        description="Founding AI engineer role with agentic systems focus.",
        extraction_status=JobExtractionStatus.PARTIAL,
        review_reasons=("partial_extraction", "missing_company"),
    )

    dumped = job.model_dump(mode="json")

    assert job.company is None
    assert dumped["extraction_status"] == "partial"
    assert dumped["review_reasons"] == ["partial_extraction", "missing_company"]


def test_compensation_rejects_invalid_bounds() -> None:
    try:
        CompensationRange(currency="USD", min_amount=200, max_amount=100)
    except ValidationError as exc:
        assert "min_amount must be <=" in str(exc)
    else:
        raise AssertionError("Expected CompensationRange validation to fail.")
