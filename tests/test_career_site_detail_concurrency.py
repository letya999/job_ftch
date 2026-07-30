from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from job_ftch.config import Settings
from job_ftch.domain import RawItem, SourceKind
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.career_site_source import CareerSiteSource


def _build_item(external_id: str) -> RawItem:
    return RawItem(
        source_kind=SourceKind.CAREER_SITE,
        source_name="jobs",
        external_id=external_id,
        url=f"https://example.com/{external_id}",
        text=f"text {external_id}",
        metadata={},
    )


@pytest.mark.asyncio
async def test_detail_scrape_respects_configured_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings.model_validate(
        {
            "career_site_default_limit": 10,
            "career_site_detail_concurrency": 2,
        }
    )
    source = CareerSiteSource(
        spec=CareerSiteSpec(url="https://example.com/jobs", source_name="jobs"),
        http_client=object(),
        auth=MagicMock(),
    )
    release = asyncio.Event()
    active = 0
    max_active = 0
    urls = [
        "https://example.com/u1",
        "https://example.com/u2",
        "https://example.com/u3",
    ]
    started: dict[str, asyncio.Event] = {url: asyncio.Event() for url in urls}

    async def _fake_scrape_detail(
        url: str,
        scraper_chain: list[str],
        source_name: str,
    ) -> RawItem | None:
        del scraper_chain, source_name
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        started[url].set()
        try:
            await release.wait()
            return _build_item(url.rsplit("/", 1)[-1])
        finally:
            active -= 1

    monkeypatch.setattr("job_ftch.config.get_settings", lambda: settings)
    monkeypatch.setattr(source, "_scrape_detail_url_to_raw_item", _fake_scrape_detail)

    iterator = source._iter_scraped_detail_items(
        urls,
        ["json-ld"],
        "jobs",
    ).__aiter__()
    first_item_task = asyncio.create_task(anext(iterator))

    await asyncio.gather(started[urls[0]].wait(), started[urls[1]].wait())
    await asyncio.sleep(0)

    assert max_active == 2
    assert not started[urls[2]].is_set()

    release.set()
    first_item = await asyncio.wait_for(first_item_task, timeout=1)
    remaining_items = [item async for item in iterator]

    assert first_item.external_id in {"u1", "u2", "u3"}
    assert sorted([first_item.external_id, *(item.external_id for item in remaining_items)]) == [
        "u1",
        "u2",
        "u3",
    ]
    assert source.stats.scraped == 3
