from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from job_ftch.config import Settings
from job_ftch.domain import RawItem, SourceKind, processed_key_for_url
from job_ftch.domain.site_models import ScrapedPostingPayload
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.career_site_source import CareerSiteSource


class _DiscoverParser:
    domain_pattern = r"^https?://mock\.com/"
    has_custom_parse = True
    supports_discover = True

    def runtime_defaults(self, url: str) -> None:
        del url
        return None

    def parser_kind(self, url: str) -> None:
        del url
        return None

    async def discover(self, spec: CareerSiteSpec, client: object) -> list[str]:
        del spec, client
        return [
            "https://mock.com/jobs/1",
            "https://mock.com/jobs/2",
            "https://mock.com/jobs/3",
        ]


def _build_item(url: str) -> RawItem:
    external_id = url.rsplit("/", 1)[-1]
    return RawItem(
        source_kind=SourceKind.CAREER_SITE,
        source_name="mock_site",
        external_id=external_id,
        url=url,
        text=f"text {external_id}",
        metadata={},
    )


class _Store:
    def __init__(self, seen_urls: set[str] | None = None) -> None:
        self.seen_keys = {
            processed_key_for_url(SourceKind.CAREER_SITE, "mock_site", url)
            for url in (seen_urls or set())
        }

    async def get_source_strategy(self, domain: str) -> None:
        del domain
        return None

    async def has_processed(self, key: str) -> bool:
        return key in self.seen_keys


def _patch_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "job_ftch.application.registry.resolve_site_parser",
        lambda url: _DiscoverParser() if "mock.com" in url else None,
    )


@pytest.mark.asyncio
async def test_discover_parser_routes_through_concurrent_detail_scrape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_parser(monkeypatch)
    settings = Settings.model_validate(
        {
            "career_site_default_limit": 10,
            "career_site_default_detail_limit": 10,
            "career_site_detail_concurrency": 2,
        }
    )
    monkeypatch.setattr("job_ftch.config.get_settings", lambda: settings)

    source = CareerSiteSource(
        spec=CareerSiteSpec(url="https://mock.com/jobs", source_name="mock_site"),
        http_client=object(),
        auth=MagicMock(),
        store=_Store(),
    )

    release = asyncio.Event()
    active = 0
    max_active = 0
    started: dict[str, asyncio.Event] = {
        url: asyncio.Event()
        for url in (
            "https://mock.com/jobs/1",
            "https://mock.com/jobs/2",
            "https://mock.com/jobs/3",
        )
    }

    async def _fake_scrape(url: str, scraper_chain: list[str], source_name: str) -> RawItem | None:
        del scraper_chain, source_name
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        started[url].set()
        try:
            await release.wait()
            return _build_item(url)
        finally:
            active -= 1

    monkeypatch.setattr(source, "_scrape_detail_url_to_raw_item", _fake_scrape)

    iterator = source.fetch().__aiter__()
    first_item_task = asyncio.create_task(anext(iterator))

    await asyncio.gather(
        started["https://mock.com/jobs/1"].wait(),
        started["https://mock.com/jobs/2"].wait(),
    )
    await asyncio.sleep(0)

    assert max_active == 2
    assert not started["https://mock.com/jobs/3"].is_set()

    release.set()
    first_item = await asyncio.wait_for(first_item_task, timeout=1)
    remaining_items = [item async for item in iterator]

    assert sorted([first_item.external_id, *(item.external_id for item in remaining_items)]) == [
        "1",
        "2",
        "3",
    ]
    assert source.stats.scraped == 3


@pytest.mark.asyncio
async def test_discover_parser_keeps_processed_locator_for_content_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_parser(monkeypatch)
    settings = Settings.model_validate({"career_site_default_limit": 10})
    monkeypatch.setattr("job_ftch.config.get_settings", lambda: settings)

    source = CareerSiteSource(
        spec=CareerSiteSpec(url="https://mock.com/jobs", source_name="mock_site"),
        http_client=object(),
        auth=MagicMock(),
        store=_Store({"https://mock.com/jobs/2"}),
    )
    scraped_urls: list[str] = []

    async def _fake_scrape(url: str, scraper_chain: list[str], source_name: str) -> RawItem | None:
        del scraper_chain, source_name
        scraped_urls.append(url)
        return _build_item(url)

    monkeypatch.setattr(source, "_scrape_detail_url_to_raw_item", _fake_scrape)

    items = [item async for item in source.fetch()]

    assert [item.external_id for item in items] == ["1", "2", "3"]
    assert scraped_urls == [
        "https://mock.com/jobs/1",
        "https://mock.com/jobs/2",
        "https://mock.com/jobs/3",
    ]


@pytest.mark.asyncio
async def test_discover_parser_respects_detail_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_parser(monkeypatch)
    settings = Settings.model_validate(
        {
            "career_site_default_limit": 10,
            "career_site_default_detail_limit": 10,
        }
    )
    monkeypatch.setattr("job_ftch.config.get_settings", lambda: settings)

    source = CareerSiteSource(
        spec=CareerSiteSpec(
            url="https://mock.com/jobs",
            source_name="mock_site",
            detail_limit=2,
        ),
        http_client=object(),
        auth=MagicMock(),
        store=_Store(),
    )

    scraped_urls: list[str] = []

    async def _fake_scrape(url: str, scraper_chain: list[str], source_name: str) -> RawItem | None:
        del scraper_chain, source_name
        scraped_urls.append(url)
        return _build_item(url)

    monkeypatch.setattr(source, "_scrape_detail_url_to_raw_item", _fake_scrape)

    items = [item async for item in source.fetch()]

    assert [item.external_id for item in items] == ["1", "2"]
    assert scraped_urls == [
        "https://mock.com/jobs/1",
        "https://mock.com/jobs/2",
    ]
    assert source.stats.truncated is True


@pytest.mark.asyncio
async def test_discover_parser_trusts_slug_only_detail_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = CareerSiteSource(
        spec=CareerSiteSpec(url="https://mock.com/jobs", source_name="mock_site"),
        http_client=object(),
        auth=MagicMock(),
    )
    url = "https://mock.com/senior-data-scientist"
    source._trusted_parser_urls.add(url)

    async def _scrape(url_value: str, chain: list[str]) -> ScrapedPostingPayload:
        del url_value, chain
        return ScrapedPostingPayload(
            title="Senior Data Scientist",
            description="Build and operate production machine-learning systems.",
        )

    monkeypatch.setattr(source, "_scrape_with_fallback", _scrape)

    item = await source._scrape_detail_url_to_raw_item(url, ["maintext"], "mock_site")

    assert item is not None
    assert str(item.url) == url


@pytest.mark.asyncio
async def test_generic_detail_drops_title_only_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = CareerSiteSource(
        spec=CareerSiteSpec(url="https://mock.com/jobs", source_name="mock_site"),
        http_client=object(),
        auth=MagicMock(),
    )

    async def _scrape(url_value: str, chain: list[str]) -> ScrapedPostingPayload:
        del url_value, chain
        return ScrapedPostingPayload(title="Editorial page")

    monkeypatch.setattr(source, "_scrape_with_fallback", _scrape)

    item = await source._scrape_detail_url_to_raw_item(
        "https://mock.com/jobs/1234", ["dom"], "mock_site"
    )

    assert item is None
