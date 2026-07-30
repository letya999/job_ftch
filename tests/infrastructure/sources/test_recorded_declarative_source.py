"""Recorded HTML and API contracts for declarative career-site extraction."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from job_ftch.infrastructure.sources.declarative import (
    CareerSiteConfig,
    DeclarativeCareerSiteParser,
    _clean_text,
    _source_name_from_url,
)

FIXTURE = (
    Path(__file__).parents[3]
    / "fixtures"
    / "real_world"
    / "site_parsers"
    / "declarative"
    / "greenhouse.html"
)


class _ApiResponse:
    def __init__(self, payload: object, *, invalid_json: bool = False) -> None:
        self._payload = payload
        self._invalid_json = invalid_json

    def json(self) -> object:
        if self._invalid_json:
            raise json.JSONDecodeError("bad", "", 0)
        return self._payload


class _ApiClient:
    def __init__(self, response: _ApiResponse) -> None:
        self.response = response
        self.urls: list[str] = []

    async def get(self, url: str) -> _ApiResponse:
        self.urls.append(url)
        return self.response


def test_declarative_helpers_and_config_from_explicit_spec() -> None:
    assert _clean_text("  platform\n engineer ") == "platform engineer"
    assert _source_name_from_url("https://jobs.example.org/careers/") == "careers"
    assert (
        CareerSiteConfig.from_spec(SimpleNamespace(parser_kind="greenhouse", url="https://x"))
        == CareerSiteConfig.greenhouse()
    )
    assert (
        CareerSiteConfig.from_spec(SimpleNamespace(parser_kind="alfabank", url="https://x"))
        == CareerSiteConfig.alfabank()
    )
    assert (
        CareerSiteConfig.from_spec(SimpleNamespace(parser_kind="unknown", url="https://x")).kind
        == "generic"
    )


@pytest.mark.asyncio
async def test_recorded_greenhouse_html_preserves_section_team_and_location() -> None:
    parser = DeclarativeCareerSiteParser(
        CareerSiteConfig(
            kind="recorded-html",
            board_selector=".job-posts",
            row_selector="h3, h4, .job-post",
            link_selector='a[href*="/jobs/"]',
            title_selector=".body--medium",
            location_selector=".body__secondary",
            section_selector="h3",
            team_selector="h4",
            href_contains="/jobs/",
            metadata_defaults={"parser": "recorded-html"},
        )
    )

    items = await parser.parse(
        client=None,
        url="https://jobs.example.org/careers",
        html=FIXTURE.read_text(encoding="utf-8"),
        limit=10,
    )

    assert len(items) == 1
    item = items[0]
    assert item.source_name == "Example Labs Careers"
    assert str(item.url) == "https://jobs.example.org/jobs/42"
    assert item.text == "Senior Platform Engineer\nRemote, Europe\nEngineering\nPlatform"
    assert item.metadata["department"] == "Engineering"
    assert item.metadata["team"] == "Platform"
    assert item.metadata["location"] == "Remote, Europe"


@pytest.mark.asyncio
async def test_declarative_html_falls_back_to_links_and_honors_limit() -> None:
    parser = DeclarativeCareerSiteParser(
        CareerSiteConfig(kind="generic", row_selector=None, link_selector="a")
    )

    items = await parser.parse(
        client=None,
        url="https://jobs.example.org/careers",
        html='<a href="/jobs/1"> First role </a><a href="/jobs/2">Second role</a><a>No URL</a>',
        limit=1,
    )

    assert [str(item.url) for item in items] == ["https://jobs.example.org/jobs/1"]
    assert items[0].text == "First role"


@pytest.mark.asyncio
async def test_declarative_api_builds_items_and_handles_invalid_payloads() -> None:
    config = CareerSiteConfig(
        kind="api",
        api_endpoint="https://api.example.org/jobs",
        json_items_path="roles",
        json_title_path="name",
        json_link_path="path",
        metadata_defaults={"parser": "api"},
    )
    client = _ApiClient(
        _ApiResponse(
            {
                "roles": [
                    {"name": "Data Engineer", "path": "/jobs/data"},
                    {"path": "/jobs/no-title"},
                    {},
                ]
            }
        )
    )
    parser = DeclarativeCareerSiteParser(config)

    items = await parser.parse(
        client=client, url="https://jobs.example.org/careers", html="ignored", limit=10
    )

    assert client.urls == ["https://api.example.org/jobs"]
    assert [str(item.url) for item in items] == [
        "https://jobs.example.org/jobs/data",
        "https://jobs.example.org/jobs/no-title",
    ]
    assert items[0].metadata == {"title": "Data Engineer", "parser": "api"}
    invalid = DeclarativeCareerSiteParser(config)
    assert (
        await invalid.parse(
            client=_ApiClient(_ApiResponse({}, invalid_json=True)),
            url="https://jobs.example.org",
            html="",
            limit=1,
        )
        == []
    )
