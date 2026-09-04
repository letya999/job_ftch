"""Keyword search-URL construction for aggregator site parsers."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import pytest

from job_ftch.infrastructure.sources.site_parsers.astanahub import AstanaHubParser
from job_ftch.infrastructure.sources.site_parsers.avito import AvitoCareerParser
from job_ftch.infrastructure.sources.site_parsers.cloud_ru import CloudRuCareerParser
from job_ftch.infrastructure.sources.site_parsers.geekjob import GeekJobParser
from job_ftch.infrastructure.sources.site_parsers.getmatch import GetmatchParser
from job_ftch.infrastructure.sources.site_parsers.habr import HabrCareerParser
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    ListingPagination,
    detect_listing_pagination,
    distinctive_search_tokens,
    keywords_from_spec,
    listing_page_url,
    normalize_search_keywords,
    paginate_listing,
    text_matches_keywords,
    url_has_search_query,
    with_query_params,
)
from job_ftch.infrastructure.sources.site_parsers.hh import HhParser
from job_ftch.infrastructure.sources.site_parsers.hirehi import HireHiParser
from job_ftch.infrastructure.sources.site_parsers.hirify import HirifyParser
from job_ftch.infrastructure.sources.site_parsers.kaspi import KaspiParser
from job_ftch.infrastructure.sources.site_parsers.large_employer_boards import X5TechCareerParser
from job_ftch.infrastructure.sources.site_parsers.ozon import OzonCareerParser
from job_ftch.infrastructure.sources.site_parsers.sber import SberParser
from job_ftch.infrastructure.sources.site_parsers.superjob import SuperJobRuParser
from job_ftch.infrastructure.sources.site_parsers.tbank import TbankCareerParser
from job_ftch.infrastructure.sources.site_parsers.vk import VkTeamParser
from job_ftch.infrastructure.sources.site_parsers.yandex import YandexJobsParser

ROLES = ["AI engineer", "LLM engineer", "ИИ инженер"]


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query, keep_blank_values=True)


def test_aggregators_declare_search_modes() -> None:
    for parser in (
        HhParser(),
        HabrCareerParser(),
        HirifyParser(),
        VkTeamParser(),
        SberParser(),
        GetmatchParser(),
        YandexJobsParser(),
        SuperJobRuParser(),
        TbankCareerParser(),
    ):
        assert parser.supports_search is True
        assert parser.search_mode == "combined"
    assert GeekJobParser().supports_search is True
    assert GeekJobParser().search_mode == "per_keyword"
    assert HireHiParser().supports_search is True
    assert HireHiParser().search_mode == "per_keyword"


def test_hh_builds_single_or_query_preserving_area() -> None:
    urls = HhParser().build_search_urls("https://hh.ru/search/vacancy?area=113", ROLES)
    assert len(urls) == 1
    parsed = urlparse(urls[0])
    query = _query(urls[0])
    assert parsed.path == "/search/vacancy"
    assert query["text"] == ["AI engineer OR LLM engineer OR ИИ инженер"]
    assert query["search_field"] == ["name"]  # title-only, avoids description noise
    assert query["ored_clusters"] == ["true"]
    assert query["area"] == ["113"]  # existing param preserved


def test_hh_normalises_root_url_to_search_path() -> None:
    urls = HhParser().build_search_urls("https://hh.kz/", ["MLOps"])
    assert urlparse(urls[0]).path == "/search/vacancy"
    assert urlparse(urls[0]).hostname == "hh.kz"


def test_habr_uses_q_and_type_all() -> None:
    urls = HabrCareerParser().build_search_urls("https://career.habr.com/vacancies", ROLES)
    query = _query(urls[0])
    assert query["q"] == ["AI engineer OR LLM engineer OR ИИ инженер"]
    assert query["type"] == ["all"]


def test_geekjob_builds_api_search_urls() -> None:
    urls = GeekJobParser().build_search_urls("https://geekjob.ru/vacancies", ROLES)
    assert len(urls) == len(ROLES)
    assert all("qs=" in url for url in urls)


def test_hirehi_builds_server_search_urls() -> None:
    urls = HireHiParser().build_search_urls("https://hirehi.ru/", ROLES)
    assert len(urls) == len(ROLES)
    assert all("search=" in url for url in urls)


def test_hirify_sets_search_with_or_operator_and_title_company() -> None:
    urls = HirifyParser().build_search_urls("https://hirify.me/jobs-in-russia", ROLES)
    query = _query(urls[0])
    # hirify honours a lowercase " or " operator; space-join matches nothing.
    assert query["search"] == ["AI engineer or LLM engineer or ИИ инженер"]
    assert query["params"] == ["title,company"]


def test_hirify_query_for_spec_forwards_search() -> None:
    from job_ftch.domain.source_spec import CareerSiteSpec

    url = HirifyParser().build_search_urls("https://hirify.me/jobs-in-russia", ["LLM"])[0]
    spec = CareerSiteSpec(url=url, source_name="hirify")
    query = HirifyParser()._query_for_spec(spec)
    assert query["search"] == "LLM"
    assert query["params"] == "title,company"
    assert query["countries"] == "russia"


def test_empty_keywords_yield_no_urls() -> None:
    for parser in (
        HhParser(),
        HabrCareerParser(),
        GeekJobParser(),
        HireHiParser(),
        HirifyParser(),
        VkTeamParser(),
        SberParser(),
        GetmatchParser(),
        YandexJobsParser(),
        SuperJobRuParser(),
    ):
        assert parser.build_search_urls("https://hh.ru/", []) == []
        assert parser.build_search_urls("https://hh.ru/", ["  ", ""]) == []


def test_vk_builds_combined_query_param() -> None:
    urls = VkTeamParser().build_search_urls("https://team.vk.company/vacancy/", ROLES)
    assert len(urls) == 1
    assert _query(urls[0])["search"] == ["AI engineer OR LLM engineer OR ИИ инженер"]


def test_sber_builds_combined_query_param() -> None:
    urls = SberParser().build_search_urls("https://rabota.sber.ru/search", ROLES)
    assert len(urls) == 1
    assert _query(urls[0])["query"] == ["AI engineer OR LLM engineer OR ИИ инженер"]


def test_normalize_search_keywords_dedupes_and_caps() -> None:
    result = normalize_search_keywords(["AI engineer", "ai  engineer", " LLM ", None, ""])
    assert result == ["AI engineer", "LLM"]
    assert len(normalize_search_keywords([f"role{i}" for i in range(30)], cap=5)) == 5


def test_url_has_search_query_detects_known_params() -> None:
    assert url_has_search_query("https://hh.ru/search/vacancy?text=LLM") is True
    assert url_has_search_query("https://career.habr.com/vacancies?q=ML&type=all") is True
    assert url_has_search_query("https://foorilla.com/hiring/jobs/?job_search=LLM") is True
    assert url_has_search_query("https://geekjob.ru/vacancies") is False
    assert url_has_search_query("https://hh.ru/search/vacancy?text=") is False  # blank ignored


def test_yandex_builds_text_or_query() -> None:
    urls = YandexJobsParser().build_search_urls("https://yandex.ru/jobs", ROLES)
    assert len(urls) == 1
    assert urlparse(urls[0]).path == "/jobs/vacancies"
    assert _query(urls[0])["text"] == ["AI engineer OR LLM engineer OR ИИ инженер"]


def test_superjob_builds_keywords_or_query() -> None:
    urls = SuperJobRuParser().build_search_urls("https://www.superjob.ru/vacancy/search/", ROLES)
    assert _query(urls[0])["keywords"] == ["AI engineer OR LLM engineer OR ИИ инженер"]


def test_tbank_keeps_it_listing_without_hardcoded_profession() -> None:
    urls = TbankCareerParser().build_search_urls("https://www.tbank.ru/career/", ROLES)
    assert len(urls) == 1
    parsed = urlparse(urls[0])
    assert parsed.path.rstrip("/") == "/career/vacancies/it"
    assert "profession" not in _query(urls[0])


def test_x5_builds_per_keyword_search_from_target_roles() -> None:
    urls = X5TechCareerParser().build_search_urls(
        "https://rabota.x5.ru/vacancies", ["LLM Engineer"]
    )
    assert _query(urls[0])["search"] == ["LLM Engineer"]
    many = X5TechCareerParser().build_search_urls("https://rabota.x5.ru/vacancies", ROLES)
    assert [_query(url)["search"] for url in many] == [[role] for role in ROLES]


def test_distinctive_search_tokens_drop_engineer() -> None:
    assert distinctive_search_tokens(ROLES) == ["AI", "LLM"]


def test_ozon_builds_combined_listing_without_query() -> None:
    urls = OzonCareerParser().build_search_urls("https://career.ozon.ru/", ROLES)
    assert urls == ["https://career.ozon.ru/vacancy/"]
    assert "query=" not in urls[0]


def test_avito_puts_target_roles_in_q() -> None:
    urls = AvitoCareerParser().build_search_urls("https://career.avito.com/vacancies/", ROLES)
    assert _query(urls[0])["q"] == ["AI engineer OR LLM engineer OR ИИ инженер"]


def test_cloud_ru_builds_per_keyword_search() -> None:
    urls = CloudRuCareerParser().build_search_urls("https://cloud.ru/career/vacancies", ROLES)
    assert [_query(url)["search"] for url in urls] == [[role] for role in ROLES]


def test_astanahub_builds_listing_without_q() -> None:
    urls = AstanaHubParser().build_search_urls("https://astanahub.com/ru/vacancy/", ["ML Engineer"])
    assert "q=" not in urls[0]
    assert _query(urls[0])["opened"] == ["True"]
    assert urlparse(urls[0]).path == "/ru/vacancy/"


def test_kaspi_puts_target_roles_in_search() -> None:
    urls = KaspiParser().build_search_urls("https://job.kaspi.kz/", ROLES)
    assert urlparse(urls[0]).path == "/search"
    assert _query(urls[0])["search"] == ["AI engineer OR LLM engineer OR ИИ инженер"]
    assert "categories" not in _query(urls[0])


def test_getmatch_keeps_listing_without_hardcoded_sphere() -> None:
    urls = GetmatchParser().build_search_urls("https://getmatch.ru/", ROLES)
    assert len(urls) == 1
    assert urls[0].startswith("https://getmatch.ru/vacancies")
    assert "sp=" not in urls[0]
    assert "query=" not in urls[0]


def test_text_matches_keywords_keeps_ml_titles() -> None:
    assert text_matches_keywords("ML-разработчик", ["ML Engineer"]) is True
    assert text_matches_keywords("Java developer", ["LLM Engineer"]) is False
    assert text_matches_keywords("anything", []) is True


def test_keywords_from_spec_read_monitor_config() -> None:
    from job_ftch.domain.source_spec import CareerSiteSpec

    spec = CareerSiteSpec(
        url="https://career.ozon.ru/",
        source_name="ozon",
        monitor_config={"_search_keywords": ["LLM Engineer", "AI Engineer"]},
    )
    assert keywords_from_spec(spec) == ["LLM Engineer", "AI Engineer"]


def test_with_query_params_overwrites_and_preserves() -> None:
    out = with_query_params("https://x.io/s?area=1&text=old", {"text": "new"})
    query = _query(out)
    assert query["text"] == ["new"]
    assert query["area"] == ["1"]


def test_listing_page_url_keeps_search_and_appends_page() -> None:
    pagination = ListingPagination(kind="page", param="page", start=1)
    start = "https://example.com/jobs?q=LLM+Engineer"
    assert listing_page_url(start, pagination, index=0) == start
    next_url = listing_page_url(start, pagination, index=1)
    assert _query(next_url)["page"] == ["2"]
    assert _query(next_url)["q"] == ["LLM Engineer"]
    offset = ListingPagination(kind="offset", param="offset", start=0, page_size=20)
    assert _query(listing_page_url("https://example.com/api", offset, index=2))["offset"] == ["40"]


def test_detect_listing_pagination_from_rel_next_and_page_links() -> None:
    next_link = detect_listing_pagination(
        '<link rel="next" href="/jobs?page=2">',
        "https://example.com/jobs",
    )
    assert next_link is not None
    assert next_link.kind == "next_url"
    page_link = detect_listing_pagination(
        '<a href="/jobs?page=2">2</a>',
        "https://example.com/jobs",
    )
    assert page_link is not None
    assert page_link.kind == "page"
    assert page_link.param == "page"
    cursor = detect_listing_pagination("{}", "https://example.com/api?cursor=abc")
    assert cursor is not None
    assert cursor.kind == "cursor"


@pytest.mark.asyncio
async def test_paginate_listing_walks_page_param() -> None:
    pages = {
        "https://example.com/jobs": '<a href="/jobs/1">one</a>',
        "https://example.com/jobs?page=2": '<a href="/jobs/2">two</a>',
        "https://example.com/jobs?page=3": '<a href="/jobs/3">three</a>',
    }

    async def fetch(url: str) -> str:
        return pages[url]

    def extract(html: str, url: str) -> list[str]:
        del url
        return re.findall(r'href="(/jobs/\d+)"', html)

    urls = await paginate_listing(
        fetch,
        extract,
        "https://example.com/jobs",
        limit=10,
        pagination=ListingPagination(kind="page", param="page", start=1, max_pages=5),
    )
    assert urls == ["/jobs/1", "/jobs/2", "/jobs/3"]


def test_x5_and_generic_parsers_declare_pagination_or_per_keyword() -> None:
    assert X5TechCareerParser().search_mode == "per_keyword"
    extra = SuperJobRuParser().runtime_defaults("https://www.superjob.ru/").extra or {}
    assert extra["pagination"]["param_name"] == "page"
