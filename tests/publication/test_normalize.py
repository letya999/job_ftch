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
    format_geo,
    format_work_mode,
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
        assert "до вычета" in result

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
        assert "на руки" in result

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


class TestFormatGeo:
    def test_city_and_country(self) -> None:
        job = _job(city="Москва", country="Россия")
        assert format_geo(job) == "Москва, Россия"

    def test_city_same_as_country_no_duplicate(self) -> None:
        job = _job(city="Singapore", country="Singapore")
        assert format_geo(job) == "Singapore"

    def test_falls_back_to_free_text_location(self) -> None:
        """Site parsers fill `location` far more often than city/country."""
        job = _job(location="Moscow; Saint Petersburg; Belgrade")
        assert format_geo(job) == "Москва, Санкт-Петербург, Белград"

    def test_free_text_comma_separated(self) -> None:
        job = _job(location="Moscow, Minsk")
        assert format_geo(job) == "Москва, Минск"

    def test_free_text_single_city(self) -> None:
        job = _job(location="Москва")
        assert format_geo(job) == "Москва"

    def test_structured_fields_win_over_free_text(self) -> None:
        job = _job(city="Berlin", location="Somewhere else")
        assert format_geo(job) == "Berlin"

    def test_free_text_deduplicated_and_capped(self) -> None:
        job = _job(location="Moscow; Moscow; Kazan; Perm; Omsk")
        assert format_geo(job) == "Москва, Казань, Perm"

    def test_no_geo_data(self) -> None:
        assert format_geo(_job()) is None


class TestGeoNormalisation:
    """Sources spell one place many ways. A feed showing "Москва, RU",
    "Moscow, Russia" and "г Москва" reads as three different places."""

    def test_country_code_expanded(self) -> None:
        assert format_geo(_job(location="Москва, RU")) == "Москва, Россия"

    def test_english_names_normalised(self) -> None:
        assert format_geo(_job(location="Moscow, Russia")) == "Москва, Россия"

    def test_settlement_prefix_stripped(self) -> None:
        assert format_geo(_job(location="г Москва")) == "Москва"
        assert format_geo(_job(location="г. Екатеринбург")) == "Екатеринбург"

    def test_all_spellings_converge(self) -> None:
        forms = ["Москва, RU", "Moscow, Russia", "Москва, Россия", "г Москва, РФ"]
        assert {format_geo(_job(location=f)) for f in forms} == {"Москва, Россия"}

    def test_cyrillic_latin_typo_in_country(self) -> None:
        """Live data contains "RФ" - latin R with a cyrillic Ф."""
        assert format_geo(_job(location="RФ")) == "Россия"

    def test_parenthetical_note_dropped(self) -> None:
        assert format_geo(_job(location="Russia (UTC+3)")) == "Россия"

    def test_work_mode_words_dropped_from_geo(self) -> None:
        """The card has a dedicated Формат row; repeating it here is noise."""
        assert format_geo(_job(location="Astana, Office")) == "Астана"

    def test_pure_work_mode_yields_no_geo(self) -> None:
        assert format_geo(_job(location="Удалённая работа (офис гибридный)")) is None

    def test_leading_mode_word_stripped_address_kept(self) -> None:
        assert format_geo(_job(location="офис на станции м. Курская")) == "на станции м. Курская"

    def test_country_field_may_hold_a_list(self) -> None:
        assert format_geo(_job(country="United Kingdom, United States")) == "Великобритания, США"

    def test_known_city_corrects_conflicting_country(self) -> None:
        assert format_geo(_job(location="Warsaw / Russia")) == "Варшава, Польша"
        assert format_geo(_job(city="Warszawa", country="Россия")) == "Варшава, Польша"

    def test_unknown_place_passes_through(self) -> None:
        """The table normalises; it must not drop places it does not know."""
        assert format_geo(_job(location="Bonnatal, Germany")) == "Bonnatal, Германия"


class TestFormatWorkMode:
    def test_remote(self) -> None:
        assert format_work_mode(_job(work_mode=WorkMode.REMOTE)) == "удалённо"

    def test_hybrid(self) -> None:
        assert format_work_mode(_job(work_mode=WorkMode.HYBRID)) == "гибрид"

    def test_onsite(self) -> None:
        assert format_work_mode(_job(work_mode=WorkMode.ONSITE)) == "офис"

    def test_unknown_returns_none(self) -> None:
        assert format_work_mode(_job()) is None

    def test_label_language_is_uniform(self) -> None:
        """Controlled vocabulary never follows the posting language: a feed
        mixing 'офис' and 'onsite' reads as broken."""
        ru = _job(work_mode=WorkMode.ONSITE, description="Разработка ML-систем")
        en = _job(work_mode=WorkMode.ONSITE, description="Building ML systems")
        assert format_work_mode(ru) == format_work_mode(en) == "офис"


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

    def test_telegram_invite_hash_not_published(self) -> None:
        """A private invite token must never reach a public post."""
        job = _job(canonical_url="https://t.me/+LHksX9WA9cs4YTQy")
        assert pick_source_label(job) == "t.me"

    def test_telegram_joinchat_not_published(self) -> None:
        job = _job(canonical_url="https://t.me/joinchat/AAAAAE0tMzM")
        assert pick_source_label(job) == "t.me"

    def test_telegram_private_channel_id_not_published(self) -> None:
        job = _job(canonical_url="https://t.me/c/1234567890/42")
        assert pick_source_label(job) == "t.me"

    def test_telegram_handle_trailing_punctuation_trimmed(self) -> None:
        job = _job(canonical_url="https://t.me/ai_engineer_jobs.")
        assert pick_source_label(job) == "t.me/ai_engineer_jobs"

    def test_subdomain_preserved(self) -> None:
        job = _job(canonical_url="https://almaty.hh.kz/vacancy/123")
        assert pick_source_label(job) == "almaty.hh.kz"

    def test_port_and_path_ignored(self) -> None:
        job = _job(canonical_url="https://jobs.example.com:8443/a/b?x=1#f")
        assert pick_source_label(job) == "jobs.example.com"


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
