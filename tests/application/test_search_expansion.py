"""Spec expansion that turns bare aggregator sources into search sources."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from job_ftch.application.search_expansion import (
    _clone_with_search_url,
    expand_career_site_specs,
    search_queries_from_target_roles,
)
from job_ftch.domain.source_spec import CareerSiteSpec, TelegramChannelSpec

ROLES = ["AI engineer", "LLM engineer"]


def test_bare_hh_source_is_rewritten_as_single_combined_query() -> None:
    specs = [CareerSiteSpec(url="https://hh.ru/search/vacancy?area=113", source_name="hh")]
    out = expand_career_site_specs(specs, ROLES)
    assert len(out) == 1  # combined -> single URL
    query = parse_qs(urlparse(out[0].url).query)
    assert query["text"] == ["AI engineer OR LLM engineer"]
    assert out[0].source_name == "hh"  # combined keeps the original name


def test_bare_geekjob_source_expands_to_role_searches() -> None:
    specs = [CareerSiteSpec(url="https://geekjob.ru/vacancies", source_name="geekjob")]
    out = expand_career_site_specs(specs, ROLES)
    assert len(out) == len(ROLES)
    assert [item.source_name for item in out] == ["geekjob_kw1", "geekjob_kw2"]
    assert [parse_qs(urlparse(item.url).query)["qs"] for item in out] == [[role] for role in ROLES]


def test_explicit_query_source_is_rebuilt_with_current_roles() -> None:
    specs = [
        CareerSiteSpec(
            url="https://hh.ru/search/vacancy?text=machine+learning&area=113",
            source_name="hh_ml",
        )
    ]
    out = expand_career_site_specs(specs, ROLES)
    assert parse_qs(urlparse(out[0].url).query)["text"] == ["AI engineer OR LLM engineer"]
    assert parse_qs(urlparse(out[0].url).query)["area"] == ["113"]


def test_locked_explicit_query_source_is_left_untouched() -> None:
    specs = [
        CareerSiteSpec(
            url="https://hh.ru/search/vacancy?text=machine+learning&area=113",
            source_name="hh_ml",
            search_locked=True,
        )
    ]
    assert expand_career_site_specs(specs, ROLES)[0].url == specs[0].url


def test_non_career_sources_pass_through() -> None:
    specs = [TelegramChannelSpec(entity="ml_jobs", source_name="tg")]
    assert expand_career_site_specs(specs, ROLES) == specs


def test_empty_roles_is_a_noop() -> None:
    specs = [CareerSiteSpec(url="https://geekjob.ru/vacancies", source_name="g")]
    assert expand_career_site_specs(specs, []) == specs


def test_vk_and_sber_expand_to_combined_query() -> None:
    specs = [
        CareerSiteSpec(url="https://team.vk.company/vacancy/", source_name="vk_careers"),
        CareerSiteSpec(url="https://rabota.sber.ru/search", source_name="rabota_sber_ru"),
    ]
    out = expand_career_site_specs(specs, ROLES)
    assert len(out) == 2
    vk_query = parse_qs(urlparse(out[0].url).query)
    sber_query = parse_qs(urlparse(out[1].url).query)
    assert vk_query["search"] == ["AI engineer OR LLM engineer"]
    assert sber_query["query"] == ["AI engineer OR LLM engineer"]
    assert out[0].source_name == "vk_careers"
    assert out[1].source_name == "rabota_sber_ru"


def test_tbank_expands_to_it_listing_with_profile_roles() -> None:
    specs = [CareerSiteSpec(url="https://www.tbank.ru/career/", source_name="tbank")]
    out = expand_career_site_specs(specs, ROLES)
    assert len(out) == 1
    parsed = urlparse(out[0].url)
    assert parsed.path.rstrip("/") == "/career/vacancies/it"
    assert "profession" not in parse_qs(parsed.query)
    assert out[0].monitor_config["_search_keywords"] == ROLES


def test_superjob_expands_to_keywords_query() -> None:
    specs = [
        CareerSiteSpec(url="https://www.superjob.ru/vacancy/search/", source_name="superjob_ru")
    ]
    out = expand_career_site_specs(specs, ROLES)
    assert len(out) == 1
    query = parse_qs(urlparse(out[0].url).query)
    assert query["keywords"] == ["AI engineer OR LLM engineer"]
    assert out[0].monitor_config["_search_keywords"] == ROLES


def test_source_without_search_parser_gets_runtime_keywords() -> None:
    # An unknown career site (no site parser) keeps its URL but receives the
    # keywords in monitor_config for Tier-1 runtime form detection.
    specs = [CareerSiteSpec(url="https://acme.example/careers", source_name="acme")]
    out = expand_career_site_specs(specs, ROLES)
    assert len(out) == 1
    assert out[0].url == "https://acme.example/careers"
    assert out[0].monitor_config["_search_keywords"] == ROLES


def test_unverified_assessment_still_uses_parser_search(monkeypatch) -> None:
    class Parser:
        supports_search = True
        search_mode = "combined"

        def build_search_urls(self, url, keywords, *, limit=None):
            del keywords, limit
            return [f"{url}?q=runtime-fallback"]

    monkeypatch.setattr(
        "job_ftch.application.registry.resolve_site_parser_for_spec", lambda _spec: Parser()
    )
    spec = CareerSiteSpec(
        url="https://example.com/jobs",
        source_name="example",
        monitor_config={"_search_assessment": {"status": "unsupported", "executor": "none"}},
    )
    out = expand_career_site_specs([spec], ROLES)
    assert out[0].url == "https://example.com/jobs?q=runtime-fallback"
    assert out[0].monitor_config["_search_keywords"] == ROLES


def test_unverified_assessment_without_parser_search_keeps_listing(monkeypatch) -> None:
    class Parser:
        supports_search = False

    monkeypatch.setattr(
        "job_ftch.application.registry.resolve_site_parser_for_spec", lambda _spec: Parser()
    )
    spec = CareerSiteSpec(
        url="https://example.com/jobs",
        source_name="example",
        monitor_config={"_search_assessment": {"status": "unsupported", "executor": "none"}},
    )
    out = expand_career_site_specs([spec], ROLES)
    assert out[0].url == "https://example.com/jobs"
    assert out[0].monitor_config["_search_keywords"] == ROLES


def test_search_queries_split_slash_and_drop_generic() -> None:
    queries = search_queries_from_target_roles(
        [
            "LLM Engineer",
            "Vibe Coder / AI Product Builder",
            "fullstack developer",
            "product management",
        ]
    )
    assert "LLM Engineer" in queries
    assert "AI Product Builder" in queries
    assert "Vibe Coder" not in queries
    assert "fullstack developer" not in queries
    assert "product management" not in queries


def test_per_keyword_fanout_is_capped(monkeypatch) -> None:
    class Parser:
        supports_search = True
        search_mode = "per_keyword"

        def build_search_urls(self, url, keywords, *, limit=None):
            del limit
            return [f"{url}?qs={term}" for term in keywords]

    monkeypatch.setattr(
        "job_ftch.application.registry.resolve_site_parser_for_spec", lambda _spec: Parser()
    )
    spec = CareerSiteSpec(url="https://geekjob.ru/vacancies", source_name="geekjob")
    roles = [
        "LLM Engineer",
        "AI Automation Engineer",
        "Agentic AI Engineer",
        "AI Product Engineer",
        "AI Architect",
    ]
    out = expand_career_site_specs([spec], roles)
    assert len(out) == 3
    assert [item.source_name for item in out] == ["geekjob_kw1", "geekjob_kw2", "geekjob_kw3"]


def test_parser_search_wins_over_verified_generic_get(monkeypatch) -> None:
    class Parser:
        supports_search = True
        search_mode = "combined"

        def build_search_urls(self, url, keywords, *, limit=None):
            del keywords, limit
            return [f"{url}?search=from-parser"]

    monkeypatch.setattr(
        "job_ftch.application.registry.resolve_site_parser_for_spec", lambda _spec: Parser()
    )
    spec = CareerSiteSpec(
        url="https://rabota.x5.ru/vacancies",
        source_name="x5",
        monitor_config={
            "_search_assessment": {
                "status": "verified",
                "executor": "generic_get",
                "query_param": "search",
            }
        },
    )
    out = expand_career_site_specs([spec], ROLES)
    assert out[0].url == "https://rabota.x5.ru/vacancies?search=from-parser"


def test_getmatch_and_yandex_expand_to_combined_query() -> None:
    specs = [
        CareerSiteSpec(url="https://getmatch.ru/vacancies", source_name="getmatch"),
        CareerSiteSpec(url="https://yandex.ru/jobs/vacancies", source_name="yandex_jobs"),
        CareerSiteSpec(url="https://hirehi.ru/", source_name="hirehi_ru"),
    ]
    out = expand_career_site_specs(specs, ROLES)
    assert urlparse(out[0].url).path.startswith("/vacancies")
    assert "sp" not in parse_qs(urlparse(out[0].url).query)
    assert out[0].monitor_config["_search_keywords"] == ROLES
    assert parse_qs(urlparse(out[1].url).query)["text"] == ["AI engineer OR LLM engineer"]
    assert len([item for item in out if item.source_name.startswith("hirehi_ru")]) == len(ROLES)


def test_habr_expands_to_combined_or_query() -> None:
    specs = [CareerSiteSpec(url="https://career.habr.com/vacancies", source_name="habr_career")]
    out = expand_career_site_specs(specs, ROLES)
    assert len(out) == 1
    query = parse_qs(urlparse(out[0].url).query)
    assert query["q"] == ["AI engineer OR LLM engineer"]
    assert query["type"] == ["all"]
    assert out[0].source_name == "habr_career"


def test_per_keyword_clone_gets_unique_source_name() -> None:
    spec = CareerSiteSpec(url="https://x.io/jobs", source_name="board")
    first = _clone_with_search_url(spec, "https://x.io/jobs?q=a", 0, 2)
    second = _clone_with_search_url(spec, "https://x.io/jobs?q=b", 1, 2)
    assert first.source_name == "board_kw1"
    assert second.source_name == "board_kw2"
