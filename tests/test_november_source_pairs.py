"""Replay reduced examples of public boards collected on 2026-11-25."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.career_site_source import CareerSiteSource
from job_ftch.infrastructure.sources.site_parsers.getmatch import item_from_detail_html

PAIRS = json.loads(
    (Path(__file__).parent / "fixtures/source_pages/november_source_pairs.json").read_text("utf-8")
)


def test_getmatch_visible_archive_notice_keeps_its_status() -> None:
    pair = PAIRS["getmatch"]
    html = pair["detail"] + "<p>Вакансия в архиве</p>"
    item = item_from_detail_html(pair["url"], html, "getmatch", pair["board"])
    assert item is not None and item.metadata["archived"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["kiat", "halykfinance", "dataart", "getmatch", "hirehi"])
async def test_board_emits_original_detail(name: str) -> None:
    pair = PAIRS[name]
    api_offsets = []

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == pair["url"]:
            return httpx.Response(200, text=pair["detail"])
        if url == pair.get("feed") or (name == "getmatch" and url.split("?")[0] == pair["feed"]):
            if name == "getmatch":
                api_offsets.append(request.url.params["offset"])
            return httpx.Response(200, json=pair["listing"])
        if url == pair["board"] and isinstance(pair["listing"], str):
            return httpx.Response(200, text=pair["listing"])
        raise AssertionError(f"Unexpected board request: {url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        source = CareerSiteSource(
            CareerSiteSpec(url=pair["board"], limit=1, bypass="noop"), client, None
        )
        items = [item async for item in source.fetch()]
    assert len(items) == 1
    item = items[0]
    assert (item.external_id, str(item.url), item.metadata["title"]) == (
        pair["id"],
        pair["url"],
        pair["title"],
    )
    assert item.metadata["detail_vacancy_confirmed"] is True
    assert source.stats.detail_cards_extracted == 1
    assert "Unrelated navigation" not in item.text
    if name == "getmatch":
        assert api_offsets == ["0"]
        assert not item.metadata.get("archived")
        assert "Управлять командой" in item.text
    elif name == "kiat":
        assert item.metadata["locations"] == ["Минск"]
        assert item.metadata["date_posted"] == "2026-08-24"
    elif name == "dataart":
        assert item.metadata["locations"] == ["Bengaluru, India"]


@pytest.mark.asyncio
async def test_hirehi_listing_fallback_is_not_counted_as_confirmed_detail() -> None:
    pair = PAIRS["hirehi"]

    def handle(request: httpx.Request) -> httpx.Response:
        if str(request.url) == pair["board"]:
            return httpx.Response(200, text=pair["listing"])
        raise AssertionError(f"Unexpected detail request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        source = CareerSiteSource(
            CareerSiteSpec(url=pair["board"], limit=1, detail_limit=0, bypass="noop"), client, None
        )
        items = [item async for item in source.fetch()]
    assert len(items) == 1
    assert items[0].metadata["detail_vacancy_confirmed"] is False
    assert source.stats.detail_cards_extracted == 0
