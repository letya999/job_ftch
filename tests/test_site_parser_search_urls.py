"""Keyword search-URL construction for aggregator site parsers."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from job_ftch.infrastructure.sources.site_parsers.geekjob import GeekJobParser
from job_ftch.infrastructure.sources.site_parsers.habr import HabrCareerParser
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    normalize_search_keywords,
    url_has_search_query,
    with_query_params,
)
from job_ftch.infrastructure.sources.site_parsers.hh import HhParser
from job_ftch.infrastructure.sources.site_parsers.hirify import HirifyParser
from job_ftch.infrastructure.sources.site_parsers.sber import SberParser
from job_ftch.infrastructure.sources.site_parsers.vk import VkTeamParser

ROLES = ["AI engineer", "LLM engineer", "ИИ инженер"]


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query, keep_blank_values=True)


def test_aggregators_declare_search_modes() -> None:
    for parser in (HhParser(), HabrCareerParser(), HirifyParser(), VkTeamParser(), SberParser()):
        assert parser.supports_search is True
        assert parser.search_mode == "combined"
    # GeekJob's search box is all-terms and matches nothing for a combined
    # multi-role query (verified live), so it fans out one URL per keyword.
    geekjob = GeekJobParser()
    assert geekjob.supports_search is True
    assert geekjob.search_mode == "per_keyword"


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


def test_geekjob_fans_out_one_url_per_keyword() -> None:
    urls = GeekJobParser().build_search_urls("https://geekjob.ru/vacancies", ROLES)
    assert len(urls) == len(ROLES)
    qs_values = [_query(url)["qs"][0] for url in urls]
    assert qs_values == ROLES  # one search URL per role, in order


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
        HirifyParser(),
        VkTeamParser(),
        SberParser(),
    ):
        assert parser.build_search_urls("https://hh.ru/", []) == []
        assert parser.build_search_urls("https://hh.ru/", ["  ", ""]) == []


def test_vk_builds_combined_query_param() -> None:
    urls = VkTeamParser().build_search_urls("https://team.vk.company/vacancy/", ROLES)
    assert len(urls) == 1
    assert _query(urls[0])["query"] == ["AI engineer OR LLM engineer OR ИИ инженер"]


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
    assert url_has_search_query("https://geekjob.ru/vacancies") is False
    assert url_has_search_query("https://hh.ru/search/vacancy?text=") is False  # blank ignored


def test_with_query_params_overwrites_and_preserves() -> None:
    out = with_query_params("https://x.io/s?area=1&text=old", {"text": "new"})
    query = _query(out)
    assert query["text"] == ["new"]
    assert query["area"] == ["1"]
