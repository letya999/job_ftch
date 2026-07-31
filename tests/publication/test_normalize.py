"""Tests for normalisation helpers."""

from __future__ import annotations

from job_ftch.domain.models import (
    CompensationPeriod,
    CompensationRange,
    Job,
    SourceKind,
    WorkMode,
)
from job_ftch.publication.normalize import (
    detect_content_language,
    format_compensation,
    format_location,
    pick_source_label,
)


def _job(**overrides: object) -> Job:
    defaults: dict[str, object] = {
        "raw_item_id": "n-1",
        "source_kind": SourceKind.CAREER_SITE,
        "source_name": "Test",
        "description": "desc",
        "title": "Engineer",
    }
    defaults.update(overrides)
    return Job(**defaults)  # type: ignore[arg-type]


class TestFormatCompensation:
    def test_range_rub(self) -> None:
        job = _job(
            compensation=CompensationRange(
                currency="RUB",
                min_amount=200000,
                max_amount=400000,
                period=CompensationPeriod.MONTH,
            )
        )
        result = format_compensation(job)
        assert result is not None
        assert "200 000" in result
        assert "400 000" in result
        assert "₽" in result
        assert "/мес" in result

    def test_single_amount(self) -> None:
        job = _job(
            compensation=CompensationRange(
                currency="USD", min_amount=5000, max_amount=5000, period=CompensationPeriod.MONTH
            )
        )
        result = format_compensation(job)
        assert result is not None
        assert "–" not in result
        assert "$" in result

    def test_only_min(self) -> None:
        job = _job(
            compensation=CompensationRange(
                currency="EUR", min_amount=3000, period=CompensationPeriod.MONTH
            )
        )
        result = format_compensation(job)
        assert result is not None
        assert "от" in result
        assert "€" in result

    def test_only_max(self) -> None:
        job = _job(
            compensation=CompensationRange(
                currency="GBP", max_amount=8000, period=CompensationPeriod.YEAR
            )
        )
        result = format_compensation(job)
        assert result is not None
        assert "до" in result
        assert "£" in result
        assert "/год" in result

    def test_gross_marker(self) -> None:
        job = _job(
            compensation=CompensationRange(
                currency="RUB",
                min_amount=300000,
                max_amount=500000,
                period=CompensationPeriod.MONTH,
                gross=True,
            )
        )
        result = format_compensation(job)
        assert result is not None
        assert "gross" in result

    def test_net_marker(self) -> None:
        job = _job(
            compensation=CompensationRange(
                currency="RUB",
                min_amount=300000,
                max_amount=500000,
                period=CompensationPeriod.MONTH,
                gross=False,
            )
        )
        result = format_compensation(job)
        assert result is not None
        assert "net" in result

    def test_no_compensation(self) -> None:
        job = _job()
        assert format_compensation(job) is None

    def test_unknown_currency_uses_iso(self) -> None:
        job = _job(
            compensation=CompensationRange(
                currency="XYZ", min_amount=1000, period=CompensationPeriod.MONTH
            )
        )
        result = format_compensation(job)
        assert result is not None
        assert "XYZ" in result

    def test_kzt_symbol(self) -> None:
        job = _job(
            compensation=CompensationRange(
                currency="KZT",
                min_amount=1000000,
                max_amount=2000000,
                period=CompensationPeriod.MONTH,
            )
        )
        result = format_compensation(job)
        assert result is not None
        assert "₸" in result


class TestFormatLocation:
    def test_remote_only(self) -> None:
        job = _job(work_mode=WorkMode.REMOTE)
        result = format_location(job)
        assert result is not None
        assert "удалённо" in result

    def test_city_and_country(self) -> None:
        job = _job(city="Москва", country="Россия")
        result = format_location(job)
        assert result is not None
        assert "Москва" in result
        assert "Россия" in result

    def test_city_country_onsite(self) -> None:
        job = _job(city="Berlin", country="Germany", work_mode=WorkMode.ONSITE)
        result = format_location(job)
        assert result is not None
        assert "Berlin" in result
        assert "офис" in result

    def test_no_location_data(self) -> None:
        job = _job()
        result = format_location(job)
        assert result is None

    def test_city_same_as_country_no_duplicate(self) -> None:
        job = _job(city="Singapore", country="Singapore")
        result = format_location(job)
        assert result is not None
        assert result.count("Singapore") == 1

    def test_english_labels(self) -> None:
        job = _job(work_mode=WorkMode.REMOTE)
        assert format_location(job, lang="en") == "remote"


class TestPickSourceLabel:
    def test_career_site_uses_domain(self) -> None:
        job = _job(canonical_url="https://career.habr.com/vacancies/1000167929")
        assert pick_source_label(job) == "career.habr.com"

    def test_www_prefix_stripped(self) -> None:
        job = _job(canonical_url="https://www.superjob.ru/vakansii/123.html")
        assert pick_source_label(job) == "superjob.ru"

    def test_telegram_keeps_channel(self) -> None:
        """Bare t.me would make every channel indistinguishable."""
        job = _job(
            source_kind=SourceKind.TELEGRAM_CHANNEL, canonical_url="https://t.me/ml_jobs_kz/972"
        )
        assert pick_source_label(job) == "t.me/ml_jobs_kz"

    def test_falls_back_to_source_name_without_url(self) -> None:
        job = _job(source_name="AI Jobs RU")
        assert pick_source_label(job) == "AI Jobs RU"


class TestDetectContentLanguage:
    def test_russian_requirements(self) -> None:
        job = _job(requirements_must=("Опыт разработки на Python от 3 лет",))
        assert detect_content_language(job) == "ru"

    def test_english_requirements(self) -> None:
        job = _job(requirements_must=("3+ years of Python backend experience",))
        assert detect_content_language(job) == "en"

    def test_english_requirements_override_ru_metadata(self) -> None:
        """A RU-tagged posting whose visible text is English must read as English."""
        from job_ftch.domain.models import LanguageCode

        job = _job(
            language=LanguageCode.RU,
            description="Стажер AI-разработчик в AGIMA",
            requirements_must=("Familiarity with llm harness", "Basic coding: python/js/ts"),
        )
        assert detect_content_language(job) == "en"

    def test_ambiguous_mix_trusts_metadata(self) -> None:
        from job_ftch.domain.models import LanguageCode

        job = _job(language=LanguageCode.RU, requirements_must=("5+ лет ML", "PyTorch", "Python"))
        assert detect_content_language(job) == "ru"

    def test_no_signal_falls_back(self) -> None:
        job = _job(description="12345 --- 678")
        assert detect_content_language(job) == "en"
