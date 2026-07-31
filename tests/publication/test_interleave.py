"""Tests for round-robin interleaving."""

from __future__ import annotations

from job_ftch.domain.models import Job, SourceKind, WorkMode
from job_ftch.publication.interleave import interleave_jobs


def _job(company: str = "A", role_family: str | None = None, source: str = "s1") -> Job:
    return Job(
        raw_item_id=f"{company}-{role_family}-{source}",
        source_kind=SourceKind.CAREER_SITE,
        source_name=source,
        description="desc",
        title=f"Engineer at {company}",
        company=company,
        role_family=role_family,
        work_mode=WorkMode.UNKNOWN,
    )


class TestInterleave:
    def test_empty(self) -> None:
        assert interleave_jobs([]) == []

    def test_single(self) -> None:
        jobs = [_job("A")]
        assert interleave_jobs(jobs) == jobs

    def test_preserves_all_items(self) -> None:
        jobs = [_job("A"), _job("A"), _job("B"), _job("B"), _job("C")]
        result = interleave_jobs(jobs)
        assert len(result) == len(jobs)

    def test_mixes_companies(self) -> None:
        jobs = [_job("A"), _job("A"), _job("A"), _job("B"), _job("B"), _job("C")]
        result = interleave_jobs(jobs)
        companies = [j.company for j in result]
        for i in range(len(companies) - 1):
            if companies[i] == companies[i + 1]:
                same_count = sum(1 for c in companies if c == companies[i])
                assert same_count > len(companies) // 3

    def test_different_sources(self) -> None:
        jobs = [
            _job("A", source="hh"),
            _job("A", source="habr"),
            _job("B", source="hh"),
            _job("B", source="habr"),
        ]
        result = interleave_jobs(jobs)
        assert len(result) == 4

    def test_all_same_company(self) -> None:
        jobs = [_job("X") for _ in range(5)]
        result = interleave_jobs(jobs)
        assert len(result) == 5
