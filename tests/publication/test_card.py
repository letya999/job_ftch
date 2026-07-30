"""Tests for PublicationCard model and build_card builder."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from job_ftch.domain.models import (
    CompensationPeriod,
    CompensationRange,
    Job,
    JobRecord,
    SkillTag,
    SourceKind,
    WorkMode,
)
from job_ftch.publication.card import PublicationCard, build_card


def _minimal_job(**overrides: object) -> Job:
    defaults: dict[str, object] = {
        "raw_item_id": "test-1",
        "source_kind": SourceKind.CAREER_SITE,
        "source_name": "TestSource",
        "description": "A test job posting",
        "title": "ML Engineer",
    }
    defaults.update(overrides)
    return Job(**defaults)  # type: ignore[arg-type]


def _minimal_record(**overrides: object) -> JobRecord:
    defaults: dict[str, object] = {
        "raw_item_id": "test-1",
        "source_kind": SourceKind.CAREER_SITE,
        "source_name": "TestSource",
        "description": "A test job posting",
        "title": "ML Engineer",
    }
    defaults.update(overrides)
    return JobRecord(**defaults)  # type: ignore[arg-type]


class TestPublicationCardModel:
    def test_minimal(self) -> None:
        card = PublicationCard(role="ML Engineer")
        assert card.role == "ML Engineer"
        assert card.company is None
        assert card.salary is None

    def test_role_required(self) -> None:
        with pytest.raises(ValueError):
            PublicationCard(role="")

    def test_frozen(self) -> None:
        card = PublicationCard(role="ML Engineer")
        with pytest.raises(ValidationError):
            card.role = "Other"  # type: ignore[misc]


class TestBuildCard:
    def test_basic_fields(self) -> None:
        job = _minimal_job(
            title="Senior ML Engineer",
            company="Acme Corp",
            city="Berlin",
            country="Germany",
            work_mode=WorkMode.REMOTE,
        )
        card = build_card(job)
        assert card.role == "Senior ML Engineer"
        assert card.company == "Acme Corp"
        assert "Berlin" in (card.location or "")
        assert card.source_name == "TestSource"

    def test_fake_company_filtered(self) -> None:
        job = _minimal_job(company="TestSource")
        card = build_card(job)
        assert card.company is None

    def test_compensation_formatting(self) -> None:
        job = _minimal_job(
            compensation=CompensationRange(
                currency="RUB",
                min_amount=200000,
                max_amount=400000,
                period=CompensationPeriod.MONTH,
            ),
        )
        card = build_card(job)
        assert card.salary is not None
        assert "200" in card.salary
        assert "400" in card.salary
        assert "₽" in card.salary
        assert "/мес" in card.salary

    def test_compensation_usd(self) -> None:
        job = _minimal_job(
            compensation=CompensationRange(
                currency="USD",
                min_amount=5000,
                max_amount=8000,
                period=CompensationPeriod.MONTH,
            ),
        )
        card = build_card(job)
        assert card.salary is not None
        assert "$" in card.salary

    def test_compensation_kzt(self) -> None:
        job = _minimal_job(
            compensation=CompensationRange(
                currency="KZT",
                min_amount=1000000,
                max_amount=2000000,
                period=CompensationPeriod.MONTH,
            ),
        )
        card = build_card(job)
        assert card.salary is not None
        assert "₸" in card.salary

    def test_compensation_single_amount(self) -> None:
        job = _minimal_job(
            compensation=CompensationRange(
                currency="RUB",
                min_amount=300000,
                max_amount=300000,
                period=CompensationPeriod.MONTH,
            ),
        )
        card = build_card(job)
        assert card.salary is not None
        assert "–" not in card.salary

    def test_compensation_gross(self) -> None:
        job = _minimal_job(
            compensation=CompensationRange(
                currency="RUB",
                min_amount=300000,
                max_amount=500000,
                period=CompensationPeriod.MONTH,
                gross=True,
            ),
        )
        card = build_card(job)
        assert card.salary is not None
        assert "gross" in card.salary

    def test_location_remote(self) -> None:
        job = _minimal_job(work_mode=WorkMode.REMOTE)
        card = build_card(job)
        assert card.location is not None
        assert "удалённо" in card.location.lower()

    def test_location_city_country(self) -> None:
        job = _minimal_job(city="Москва", country="Россия", work_mode=WorkMode.ONSITE)
        card = build_card(job)
        assert card.location is not None
        assert "Москва" in card.location
        assert "Россия" in card.location
        assert "офис" in card.location.lower()

    def test_stack_from_tools(self) -> None:
        record = _minimal_record(tools_stack=("Python", "PyTorch", "Docker"))
        card = build_card(record)
        assert card.stack is not None
        assert "Python" in card.stack
        assert "PyTorch" in card.stack

    def test_stack_from_skills_fallback(self) -> None:
        record = _minimal_record(
            skills_explicit=(
                SkillTag(canonical_name="Python"),
                SkillTag(canonical_name="TensorFlow"),
            ),
        )
        card = build_card(record)
        assert card.stack is not None
        assert "Python" in card.stack

    def test_requirements(self) -> None:
        record = _minimal_record(
            requirements_must=("3+ years ML", "PyTorch experience", "English B2"),
        )
        card = build_card(record)
        assert card.key_requirements is not None
        assert "3+ years ML" in card.key_requirements

    def test_url_from_canonical(self) -> None:
        job = _minimal_job(canonical_url="https://example.com/job/123")
        card = build_card(job)
        assert card.url == "https://example.com/job/123"

    def test_language_detection(self) -> None:
        from job_ftch.domain.models import LanguageCode

        job = _minimal_job(language=LanguageCode.RU)
        card = build_card(job)
        assert card.language == "ru"

    def test_source_kind_preserved(self) -> None:
        job = _minimal_job(source_kind=SourceKind.TELEGRAM_CHANNEL)
        card = build_card(job)
        assert card.source_kind == "telegram_channel"
