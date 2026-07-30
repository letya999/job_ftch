from types import SimpleNamespace

import pytest

from job_ftch.adapters.telegram_bot.formatter import (
    _is_fake_company,
    _safe_href,
    _safe_truncate_html,
    format_job_digest,
    format_vacancy_card,
)


@pytest.mark.unit
def test_is_fake_company():
    # Returns True when company is None or empty string
    assert _is_fake_company(None, "source") is True
    assert _is_fake_company("", "source") is True

    # Returns True when company == source_name (case-insensitive)
    assert _is_fake_company("MySource", "mysource") is True

    # Returns True for technical slugs: "hh_com", "habr.com", "hh.ru"
    assert _is_fake_company("hh_com", "source") is True
    assert _is_fake_company("habr.com", "source") is True
    assert _is_fake_company("hh.ru", "source") is True

    # Returns True for placeholder values: "unknown", "недоступно", "—", "-", "n/a"
    assert _is_fake_company("unknown", "source") is True
    assert _is_fake_company("недоступно", "source") is True
    assert _is_fake_company("—", "source") is True
    assert _is_fake_company("-", "source") is True
    assert _is_fake_company("n/a", "source") is True

    # Returns True when company contains HTML fragments ("<b>")
    assert _is_fake_company("Company <b>Inc</b>", "source") is True

    # Returns True when len(company) > 50
    assert _is_fake_company("a" * 51, "source") is True

    # Returns True when company has >= 6 spaces (sentence-like)
    assert _is_fake_company("this is a very long sentence about a company", "source") is True

    # Returns False for a normal short company name like "Google", "Яндекс"
    assert _is_fake_company("Google", "source") is False
    assert _is_fake_company("Яндекс", "source") is False


@pytest.mark.unit
def test_safe_href():
    # Returns None for None input
    assert _safe_href(None) is None

    # Returns None for empty string
    assert _safe_href("") is None

    # Returns None for javascript:alert('xss') (unsafe scheme)
    assert _safe_href("javascript:alert('xss')") is None

    # Returns None for ftp://example.com (unsafe scheme)
    assert _safe_href("ftp://example.com") is None

    # Returns sanitized string for https://example.com/job?q=1&lang=ru (& → &amp;)
    assert (
        _safe_href("https://example.com/job?q=1&lang=ru")
        == "https://example.com/job?q=1&amp;lang=ru"
    )

    # Returns sanitized string for http://... and t.me/... links
    assert _safe_href("http://example.com") == "http://example.com"
    assert _safe_href("t.me/example") == "t.me/example"


def _make_job(**kwargs):
    defaults = {
        "title": "Software Engineer",
        "company": "Tech Corp",
        "work_mode": "remote",
        "location": "NY",
        "description": "A great job",
        "canonical_url": "https://example.com",
        "urls": [],
        "compensation": None,
        "source_name": "source",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.mark.unit
def test_format_vacancy_card_basic():
    # 1. Basic card — title present, no company (fake), no url → output starts with "🔵 <b>"
    job = _make_job(company="", canonical_url=None)
    card = format_vacancy_card(job)
    assert card.startswith("🔵 <b>")
    assert job.title in card


@pytest.mark.unit
def test_format_vacancy_card_remote_work_mode():
    # 2. Remote work_mode "remote" → contains "🌍 Remote"
    job = _make_job(work_mode="remote")
    card = format_vacancy_card(job)
    assert "🌍 Remote" in card


@pytest.mark.unit
def test_format_vacancy_card_office_work_mode():
    # 3. Office work_mode "офис" → contains "🏢 Office"
    job = _make_job(work_mode="офис")
    card = format_vacancy_card(job)
    assert "🏢 Office" in card


@pytest.mark.unit
def test_format_vacancy_card_hybrid_work_mode():
    # 4. Hybrid work_mode "hybrid" → contains "🔀 Hybrid"
    job = _make_job(work_mode="hybrid")
    card = format_vacancy_card(job)
    assert "🔀 Hybrid" in card


@pytest.mark.unit
def test_format_vacancy_card_unknown_work_mode():
    # 5. Unknown work_mode → no work_mode label in output
    job = _make_job(work_mode="unknown")
    card = format_vacancy_card(job)
    assert "🌍 Remote" not in card
    assert "🏢 Office" not in card
    assert "🔀 Hybrid" not in card


@pytest.mark.unit
def test_format_vacancy_card_real_company():
    # 6. Real company name → appears in output with "🏢 " prefix
    job = _make_job(company="Google")
    card = format_vacancy_card(job)
    assert "🏢 " in card
    assert "Google" in card


@pytest.mark.unit
def test_format_vacancy_card_fake_company():
    # 7. Fake company (== source_name) → "🏢 company_name" NOT in output
    job = _make_job(company="mysource", source_name="mysource")
    card = format_vacancy_card(job)
    assert "🏢 mysource" not in card


@pytest.mark.unit
def test_format_vacancy_card_safe_url():
    # 8. Safe URL present → contains "Открыть вакансию"
    job = _make_job(canonical_url="https://example.com/job")
    card = format_vacancy_card(job)
    assert "Открыть вакансию" in card


@pytest.mark.unit
def test_format_vacancy_card_unsafe_url():
    # 9. Unsafe URL (javascript:...) → "Открыть вакансию" NOT in output
    job = _make_job(canonical_url="javascript:alert(1)")
    card = format_vacancy_card(job)
    assert "Открыть вакансию" not in card


@pytest.mark.unit
def test_format_vacancy_card_compensation():
    # 10. Compensation with min+max → contains "💰"
    job = _make_job(compensation=SimpleNamespace(min_amount=100, max_amount=200, currency="USD"))
    card = format_vacancy_card(job)
    assert "💰" in card


@pytest.mark.unit
def test_format_vacancy_card_long_description():
    # 11. Long description → description is truncated (len <= 3800)
    job = _make_job(description="A" * 4000)
    card = format_vacancy_card(job)
    assert len(card) < 4000
    assert "<i>" in card


@pytest.mark.unit
def test_format_vacancy_card_no_description():
    # 12. No description → no <i> tag in output
    job = _make_job(description="")
    card = format_vacancy_card(job)
    assert "<i>" not in card


@pytest.mark.unit
def test_truncation_does_not_break_html_entity() -> None:
    truncated = _safe_truncate_html("x" * 20 + "&amp;", 24)
    assert not truncated.endswith("&")
    assert "&am" not in truncated


@pytest.mark.unit
def test_truncation_does_not_break_open_tag() -> None:
    truncated = _safe_truncate_html("x" * 20 + '<a href="https://example.com">', 28)
    assert "<a href" not in truncated


@pytest.mark.unit
def test_format_job_digest_empty():
    # Empty list → returns "No jobs available."
    assert format_job_digest([], page=0, page_size=10) == "No jobs available."


@pytest.mark.unit
def test_format_job_digest_single():
    # Single job, page=0 → returns format_vacancy_card of first job
    job = _make_job()
    card = format_vacancy_card(job)
    assert format_job_digest([job], page=0, page_size=10) == card


@pytest.mark.unit
def test_format_job_digest_out_of_range():
    # page=1, page_size=1 with 1 job → "No jobs available." (out of range)
    job = _make_job()
    assert format_job_digest([job], page=1, page_size=1) == "No jobs available."
