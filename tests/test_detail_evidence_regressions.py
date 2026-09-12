import json

import pytest

from job_ftch.application.geo import normalize_geo_sources
from job_ftch.infrastructure.sources.career_site_source import (
    _has_vacancy_page_evidence,
    _is_valid_detail_candidate,
)
from job_ftch.infrastructure.sources.scrapers.json_ld import parse_html
from job_ftch.infrastructure.sources.site_parsers.getmatch import (
    extract_vacancy_urls_from_offers,
    item_from_detail_html,
)


def test_structured_company_and_fixed_salary_survive_acquisition():
    posting = {
        "@type": "JobPosting",
        "title": "AI Developer",
        "description": "Full job description",
        "hiringOrganization": {"name": "Real Employer"},
        "baseSalary": {"currency": "RUB", "value": {"value": 250000, "unitText": "MONTH"}},
    }
    result = parse_html('<script type="application/ld+json">' + json.dumps(posting) + "</script>")
    assert result.metadata["company"] == "Real Employer"
    assert result.base_salary["min"] == result.base_salary["max"] == 250000
    assert result.metadata["detail_vacancy_confirmed"] is False


def test_getmatch_search_uses_position_not_only_slug():
    payload = {"offers": [{"id": 123, "position": "AI Developer", "url": "/vacancies/123"}]}
    assert extract_vacancy_urls_from_offers(payload, limit=3, keywords=["AI Developer"]) == [
        "https://getmatch.ru/vacancies/123"
    ]


def test_getmatch_markdown_detail_is_not_replaced_by_announcement():
    item = item_from_detail_html(
        "https://getmatch.ru/vacancies/123",
        "<h1>AI Developer</h1>"
        '<div class="b-vacancy"><div class="markdown">Full requirements and responsibilities</div></div>'
        '<meta name="description" content="Short announcement">',
        "getmatch",
        "https://getmatch.ru/vacancies",
    )
    assert "Full requirements and responsibilities" in item.text
    assert "Short announcement" not in item.text
    assert item.metadata["detail_vacancy_confirmed"] is True
    short = item_from_detail_html(
        "https://getmatch.ru/vacancies/123",
        '<h1>AI Developer</h1><div class="b-vacancy"></div>'
        '<meta name="description" content="Short announcement">',
        "getmatch",
        "https://getmatch.ru/vacancies",
    )
    assert short.metadata["detail_vacancy_confirmed"] is False


def test_astana_country_conflict_is_corrected_and_recorded():
    geo = normalize_geo_sources(("Астана", "Россия"))
    assert (geo.city, geo.country, geo.display) == ("Астана", "Казахстан", "Астана, Казахстан")
    assert geo.corrections == ("country:city_conflict:Россия->Казахстан",)
    assert normalize_geo_sources(("Москва, Астана, Россия, Казахстан",)).corrections == ()
    assert normalize_geo_sources(("Unknown City, Россия",)).country == "Россия"
    assert normalize_geo_sources(("Москва (Метро) Удалённо",)).city == "Москва"
    assert normalize_geo_sources(("АСТАНА", "РОССИЯ")).country == "Казахстан"


def test_ats_host_is_not_source_ownership_evidence():
    assert not _is_valid_detail_candidate(
        "https://job-boards.greenhouse.io/zscaler/jobs/123", "https://remote.com/careers"
    )
    assert not _is_valid_detail_candidate(
        "https://evil.example/jobs/123?redirect=greenhouse.io", "https://remote.com/careers"
    )


def test_long_marketing_text_is_not_vacancy_page_evidence():
    from job_ftch.domain.site_models import ScrapedPostingPayload

    assert not _has_vacancy_page_evidence(
        ScrapedPostingPayload(title="Special offers", description="Our offers and products. " * 200)
    )
    assert _has_vacancy_page_evidence(
        ScrapedPostingPayload(
            title="Developer", description="Responsibilities: build. Requirements: Python."
        )
    )


def test_rich_payload_cannot_change_source_ownership():
    from unittest.mock import MagicMock

    from job_ftch.domain.site_models import DiscoveredPostingPayload, MonitorResult
    from job_ftch.domain.source_spec import CareerSiteSpec
    from job_ftch.infrastructure.sources.career_site_source import CareerSiteSource

    source = CareerSiteSource(
        CareerSiteSpec(url="https://remote.com/careers"), MagicMock(), MagicMock()
    )
    source.spec = source.spec.model_copy(update={"url": "https://job-boards.greenhouse.io/zscaler"})
    url = "https://job-boards.greenhouse.io/zscaler/jobs/123"
    payload = DiscoveredPostingPayload(url=url, title="Developer", description="Job body " * 100)
    result = MonitorResult(urls={url}, payloads_by_url={url: payload})
    assert source._discover_candidates(result, "greenhouse") == []
    assert source.stats.source_partial is True
    assert source.stats.truncated is True


def test_generic_dom_keeps_requirements_and_long_tail():
    from job_ftch.infrastructure.sources.dom_utils import flatten, walk_steps
    from job_ftch.infrastructure.sources.scrapers.dom import can_handle

    html = "<h1>Developer</h1>" + "<p>Actual job duties.</p>" * 60
    html += "<h2>Requirements</h2><p>Python and PostgreSQL are required.</p>"
    config = can_handle([html])
    raw, _ = walk_steps(flatten(html), config["steps"])
    assert "Python and PostgreSQL" in raw["description"]


def test_salary_without_currency_does_not_become_rubles():
    from job_ftch.nodes.job_normalization import _parse_compensation_text

    assert _parse_compensation_text("Зарплата от 300к") is None
    assert _parse_compensation_text("от 300к рублей") == ("RUB", 300000, None)
    assert _parse_compensation_text("200 000–350 000 ₸") == ("KZT", 200000, 350000)


@pytest.mark.asyncio
async def test_authoritative_employer_survives_wrong_extractor(make_raw_item):
    from job_ftch.infrastructure.llm.heuristic import HeuristicLLMProvider
    from job_ftch.nodes.extraction import ExtractionNode

    raw = make_raw_item(
        text="Дизайнер-верстальщик\nКомпания Ресурс ищет сотрудника",
        metadata={"company": "Ресурс", "company_authoritative": True},
    )
    draft = await ExtractionNode(HeuristicLLMProvider()).process(raw)
    assert draft.company_name_raw == "Ресурс"


@pytest.mark.asyncio
async def test_structured_salary_unit_survives_normalization(make_job_record):
    from job_ftch.nodes.job_normalization import CompensationParsingNode

    record = make_job_record(
        metadata={"base_salary": {"currency": "RUB", "min": 250000, "max": 250000, "unit": "MONTH"}}
    )
    final = await CompensationParsingNode().process(record)
    assert final.compensation.period.value == "month"
    assert final.compensation.currency == "RUB"


@pytest.mark.asyncio
async def test_country_is_not_a_city(make_job_record):
    from job_ftch.nodes.job_normalization import LocationWorkModeNormalizationNode

    final = await LocationWorkModeNormalizationNode().process(
        make_job_record(location="Russia", city="Россия", country="Россия")
    )
    assert final.country == "Россия"
    assert final.city is None
    numeric = await LocationWorkModeNormalizationNode().process(
        make_job_record(location="6", city="6", region="6", country=None)
    )
    assert (numeric.location, numeric.city, numeric.region) == (None, None, None)


@pytest.mark.asyncio
async def test_post_accept_enrichment_cannot_restore_invalid_geo(make_job_record):
    from job_ftch.domain import MatchDecision
    from job_ftch.nodes.full_extraction import FullExtractionNode

    class Provider:
        async def extract(self, text, schema):
            return schema(
                title="Developer",
                company="Employer",
                description="Build products",
                location="Астана",
            )

    job = make_job_record(
        location="Астана", city="Астана", country="Россия", routing_decision=MatchDecision.ACCEPT
    )
    final = await FullExtractionNode(Provider()).process(job)
    assert (final.city, final.country, final.location) == (
        "Астана",
        "Казахстан",
        "Астана, Казахстан",
    )
    assert final.metadata["geo_conflict_original_country"] == "Россия"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,currency,amount,gross",
    [
        ("from 3 000 $/\u200dmonth gross", "USD", 3000, True),
        ("от 200 000 ₽/\u200dмес на руки", "RUB", 200000, False),
        ("от 450 000 ₽/\u200dмес на руки", "RUB", 450000, False),
    ],
)
async def test_real_getmatch_salary_headers_keep_period_and_tax(
    make_job_record, text, currency, amount, gross
):
    from job_ftch.nodes.job_normalization import CompensationParsingNode

    final = await CompensationParsingNode().process(
        make_job_record(metadata={"base_salary_text": text})
    )
    salary = final.compensation
    assert (salary.currency, salary.min_amount, salary.max_amount) == (currency, amount, None)
    assert salary.period.value == "month"
    assert salary.gross is gross
