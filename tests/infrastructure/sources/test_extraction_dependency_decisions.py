from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from job_ftch.infrastructure.sources.career_site_source import _parse_source_datetime
from job_ftch.infrastructure.sources.scrapers import xpath
from job_ftch.nodes.job_normalization import _parse_compensation_text

_CORPUS_PATH = Path(__file__).parents[2] / "fixtures" / "extraction" / "ingest_values.json"

# Relative/natural-language dates are parsed through the optional ``dateparser``
# dependency; ``_parse_source_datetime`` degrades gracefully to None when it is
# absent. Skip those cases (rather than hard-fail) when the dep is not installed.
_HAS_DATEPARSER = importlib.util.find_spec("dateparser") is not None
_needs_dateparser = pytest.mark.skipif(
    not _HAS_DATEPARSER, reason="dateparser not installed; relative dates degrade to None"
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(
            "2 days ago", datetime(2026, 7, 18, 12, 0, tzinfo=UTC), marks=_needs_dateparser
        ),
        pytest.param("вчера", datetime(2026, 7, 19, 12, 0, tzinfo=UTC), marks=_needs_dateparser),
        ("2026-07-01", datetime(2026, 7, 1, 0, 0, tzinfo=UTC)),
    ],
)
def test_multilingual_date_fixture(value: str, expected: datetime) -> None:
    base = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    assert _parse_source_datetime(value, relative_base=base) == expected


def test_compensation_corpus_covers_ranges_suffix_currency_and_k_suffix() -> None:
    corpus = json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))
    for case in corpus["compensation"]:
        assert _parse_compensation_text(case["input"]) == (
            case["currency"],
            case["min"],
            case["max"],
        )


@pytest.mark.asyncio
async def test_xpath_fixture_extracts_vacancy_with_optional_backend() -> None:
    fixture = (
        Path(__file__).parents[2] / "fixtures" / "extraction" / "vacancy_xpath.html"
    ).read_text(encoding="utf-8")
    result = await xpath.scrape(
        "https://example.test/jobs/1",
        {
            "prefetched_html": fixture,
            "xpath_rules": {
                "title": "//h1/text()",
                "description": "//article[contains(@class, 'job-description')]//text()",
                "location": "//*[contains(@class, 'location')]//text()",
                "employment_type": "//*[contains(@class, 'employment')]//text()",
            },
        },
        SimpleNamespace(),
    )
    assert result is not None
    assert result.title == "Senior Python Engineer"
    assert "ingestion pipeline" in (result.description or "")
    assert result.locations == ["Москва", "Remote"]


def test_xpath_scraper_has_graceful_missing_optional_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(xpath, "_PARSEL_AVAILABLE", False)
    monkeypatch.setattr(xpath, "_LXML_AVAILABLE", False)
    assert not xpath.can_handle(
        "https://example.test/jobs/1",
        {"xpath_rules": {"title": "//h1/text()"}},
    )
