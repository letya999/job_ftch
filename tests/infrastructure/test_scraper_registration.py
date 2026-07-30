"""Registration-level tests for scrapers and URL filter chain.

These guard against the class of bug where a scraper module exists and defines
``register_scraper(...)`` but is never imported by the loader, leaving it dead.
"""

from __future__ import annotations

import pytest

from job_ftch.domain.site_models import DiscoveredPostingPayload, MonitorResult
from job_ftch.infrastructure.sources.site_utils import (
    URLFilterChain,
    apply_url_filter,
    build_filter_chain_from_config,
)


class TestScraperRegistration:
    def test_maintext_registered_when_extraction_available(self) -> None:
        pytest.importorskip("trafilatura")

        from job_ftch.application import registry
        from job_ftch.infrastructure.sources.scrapers import load_scrapers

        load_scrapers()
        assert "maintext" in registry._SCRAPER_REGISTRY

    def test_core_structured_scrapers_registered(self) -> None:
        from job_ftch.application import registry
        from job_ftch.infrastructure.sources.scrapers import load_scrapers

        load_scrapers()
        assert "json-ld" in registry._SCRAPER_REGISTRY
        assert "dom" in registry._SCRAPER_REGISTRY


def _result(*urls: str) -> MonitorResult:
    payloads = {u: DiscoveredPostingPayload(url=u) for u in urls}
    return MonitorResult(urls=set(urls), payloads_by_url=payloads)


class TestURLFilterChain:
    def test_include_exclude(self) -> None:
        chain = URLFilterChain().include(r"/jobs/").exclude(r"\.pdf$")
        assert chain.matches("https://x.com/jobs/1")
        assert not chain.matches("https://x.com/about")
        assert not chain.matches("https://x.com/jobs/1.pdf")

    def test_require_suffix(self) -> None:
        chain = URLFilterChain().require_suffix(".html")
        assert chain.matches("https://x.com/a.html")
        assert not chain.matches("https://x.com/a.json")

    def test_require_prefix(self) -> None:
        chain = URLFilterChain().require_prefix("/vacancy")
        assert chain.matches("https://x.com/vacancy/12")
        assert not chain.matches("https://x.com/blog/12")

    def test_apply_updates_filtered_count(self) -> None:
        result = _result("https://x.com/jobs/1", "https://x.com/about")
        chain = URLFilterChain().include(r"/jobs/")
        out = chain.apply(result)
        assert out.urls == {"https://x.com/jobs/1"}
        assert out.filtered_count == 1


class TestApplyUrlFilterDelegation:
    def test_bare_string_is_include(self) -> None:
        result = _result("https://x.com/jobs/1", "https://x.com/about")
        out = apply_url_filter(result, r"/jobs/")
        assert out.urls == {"https://x.com/jobs/1"}

    def test_dict_include_exclude(self) -> None:
        result = _result("https://x.com/jobs/1", "https://x.com/jobs/1.pdf")
        out = apply_url_filter(result, {"include": r"/jobs/", "exclude": r"\.pdf$"})
        assert out.urls == {"https://x.com/jobs/1"}

    def test_dict_suffix_now_supported(self) -> None:
        # Previously impossible via apply_url_filter; now reachable through the chain.
        result = _result("https://x.com/a.html", "https://x.com/a.json")
        out = apply_url_filter(result, {"suffix": ".html"})
        assert out.urls == {"https://x.com/a.html"}

    def test_none_config_is_noop(self) -> None:
        result = _result("https://x.com/a", "https://x.com/b")
        out = apply_url_filter(result, None)
        assert out.urls == {"https://x.com/a", "https://x.com/b"}

    def test_list_is_or_include_set(self) -> None:
        result = _result("https://x.com/jobs/1", "https://x.com/careers/2", "https://x.com/blog/3")
        chain = build_filter_chain_from_config([r"/jobs/", r"/careers/"])
        assert chain is not None
        # list = OR of includes; keep urls matching ANY pattern
        out = chain.apply(result)
        assert out.urls == {"https://x.com/jobs/1", "https://x.com/careers/2"}
