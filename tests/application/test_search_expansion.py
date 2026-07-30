"""Spec expansion that turns bare aggregator sources into search sources."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from job_ftch.application.search_expansion import (
    _clone_with_search_url,
    expand_career_site_specs,
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


def test_bare_geekjob_source_fans_out_per_keyword() -> None:
    specs = [CareerSiteSpec(url="https://geekjob.ru/vacancies", source_name="geekjob")]
    out = expand_career_site_specs(specs, ROLES)
    assert len(out) == len(ROLES)  # per_keyword -> one source per role
    assert [s.source_name for s in out] == ["geekjob_kw1", "geekjob_kw2"]
    qs_values = [parse_qs(urlparse(s.url).query)["qs"][0] for s in out]
    assert qs_values == ROLES


def test_explicit_query_source_is_left_untouched() -> None:
    specs = [
        CareerSiteSpec(
            url="https://hh.ru/search/vacancy?text=machine+learning&area=113",
            source_name="hh_ml",
        )
    ]
    out = expand_career_site_specs(specs, ROLES)
    assert out == specs  # idempotent: hand-authored query preserved


def test_non_career_sources_pass_through() -> None:
    specs = [TelegramChannelSpec(entity="ml_jobs", source_name="tg")]
    assert expand_career_site_specs(specs, ROLES) == specs


def test_empty_roles_is_a_noop() -> None:
    specs = [CareerSiteSpec(url="https://geekjob.ru/vacancies", source_name="g")]
    assert expand_career_site_specs(specs, []) == specs


def test_source_without_search_parser_gets_runtime_keywords() -> None:
    # An unknown career site (no site parser) keeps its URL but receives the
    # keywords in monitor_config for Tier-1 runtime form detection.
    specs = [CareerSiteSpec(url="https://acme.example/careers", source_name="acme")]
    out = expand_career_site_specs(specs, ROLES)
    assert len(out) == 1
    assert out[0].url == "https://acme.example/careers"
    assert out[0].monitor_config["_search_keywords"] == ROLES


def test_per_keyword_clone_gets_unique_source_name() -> None:
    spec = CareerSiteSpec(url="https://x.io/jobs", source_name="board")
    first = _clone_with_search_url(spec, "https://x.io/jobs?q=a", 0, 2)
    second = _clone_with_search_url(spec, "https://x.io/jobs?q=b", 1, 2)
    assert first.source_name == "board_kw1"
    assert second.source_name == "board_kw2"
