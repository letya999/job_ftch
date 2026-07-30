"""Unit tests for the source URL filter chain."""

from __future__ import annotations

from job_ftch.infrastructure.sources.site_utils import URLFilterChain


class TestURLFilterChain:
    def test_include_filters_by_keyword(self) -> None:
        chain = URLFilterChain().include("jobs")
        urls = ["https://example.com/jobs", "https://example.com/about"]
        assert [url for url in urls if chain.matches(url)] == ["https://example.com/jobs"]

    def test_exclude_filters_out_patterns(self) -> None:
        chain = URLFilterChain().exclude(r"/archive|/new")
        urls = ["https://example.com/jobs", "https://example.com/archive/2020"]
        assert [url for url in urls if chain.matches(url)] == ["https://example.com/jobs"]

    def test_require_suffix_filters_by_extension(self) -> None:
        chain = URLFilterChain().require_suffix(".html", ".htm")
        urls = [
            "https://example.com/jobs.html",
            "https://example.com/data.json",
            "https://example.com/index.htm",
        ]
        assert [url for url in urls if chain.matches(url)] == [
            "https://example.com/jobs.html",
            "https://example.com/index.htm",
        ]

    def test_chained_filters_compose(self) -> None:
        chain = URLFilterChain().include("jobs").exclude(r"/archive").require_suffix(".html")
        urls = [
            "https://example.com/jobs.html",
            "https://example.com/jobs.json",
            "https://example.com/jobs/archive.html",
            "https://example.com/jobs/listing.html",
        ]
        assert [url for url in urls if chain.matches(url)] == [
            "https://example.com/jobs.html",
            "https://example.com/jobs/listing.html",
        ]

    def test_empty_chain_matches_all(self) -> None:
        urls = ["https://a.com", "https://b.com"]
        assert [url for url in urls if URLFilterChain().matches(url)] == urls

    def test_custom_predicate(self) -> None:
        chain = URLFilterChain().custom(lambda url: "apply" not in url)
        urls = ["https://example.com/jobs", "https://example.com/apply/123"]
        assert [url for url in urls if chain.matches(url)] == ["https://example.com/jobs"]

    def test_url_filter_require_prefix(self) -> None:
        chain = URLFilterChain().require_prefix("/jobs/", "/careers/")
        urls = [
            "https://example.com/jobs/123",
            "https://example.com/careers/456",
            "https://example.com/about",
        ]
        assert [url for url in urls if chain.matches(url)] == [
            "https://example.com/jobs/123",
            "https://example.com/careers/456",
        ]
