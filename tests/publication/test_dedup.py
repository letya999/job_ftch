"""Tests for fingerprint-based publication dedup."""

from __future__ import annotations

from job_ftch.domain.models import CompensationPeriod, CompensationRange, Job, SourceKind, WorkMode
from job_ftch.publication.dedup import deduplicate_for_publish


def _job(
    title: str = "ML Engineer",
    company: str = "Acme",
    source: str = "s1",
    source_kind: SourceKind = SourceKind.TELEGRAM_CHANNEL,
    salary_min: int | None = None,
    salary_max: int | None = None,
) -> Job:
    comp = None
    if salary_min is not None or salary_max is not None:
        comp = CompensationRange(
            currency="RUB",
            min_amount=salary_min,
            max_amount=salary_max or salary_min,
            period=CompensationPeriod.MONTH,
        )
    return Job(
        raw_item_id=f"{title}-{company}-{source}",
        source_kind=source_kind,
        source_name=source,
        description="desc",
        title=title,
        company=company,
        compensation=comp,
        work_mode=WorkMode.UNKNOWN,
    )


class TestDedup:
    def test_no_duplicates(self) -> None:
        jobs = [_job("A", "X"), _job("B", "Y")]
        result = deduplicate_for_publish(jobs)
        assert len(result) == 2

    def test_exact_duplicate_removed(self) -> None:
        jobs = [_job("ML Engineer", "Acme"), _job("ML Engineer", "Acme")]
        result = deduplicate_for_publish(jobs)
        assert len(result) == 1

    def test_career_site_preferred(self) -> None:
        tg = _job("ML Engineer", "Acme", source_kind=SourceKind.TELEGRAM_CHANNEL)
        career = _job("ML Engineer", "Acme", source_kind=SourceKind.CAREER_SITE)
        result = deduplicate_for_publish([tg, career])
        assert len(result) == 1
        assert result[0].source_kind == SourceKind.CAREER_SITE

    def test_different_salary_not_deduped(self) -> None:
        a = _job("ML Engineer", "Acme", salary_min=200000)
        b = _job("ML Engineer", "Acme", salary_min=300000)
        result = deduplicate_for_publish([a, b])
        assert len(result) == 2

    def test_different_company_not_deduped(self) -> None:
        a = _job("ML Engineer", "Acme")
        b = _job("ML Engineer", "Beta")
        result = deduplicate_for_publish([a, b])
        assert len(result) == 2

    def test_empty(self) -> None:
        assert deduplicate_for_publish([]) == []

    def test_case_insensitive(self) -> None:
        a = _job("ml engineer", "acme")
        b = _job("ML Engineer", "Acme")
        result = deduplicate_for_publish([a, b])
        assert len(result) == 1
