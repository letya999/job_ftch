from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
import yaml

from job_ftch.domain.models import RawItem
from job_ftch.domain.source_spec import RSSFeedSourceSpec
from job_ftch.infrastructure.sources.realtime.rss import RSSFeedSource


@pytest.fixture
def habr_ml_spec() -> RSSFeedSourceSpec:
    path = Path(__file__).parent.parent.parent / "fixtures" / "specs" / "rss_habr_ml.yaml"
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return RSSFeedSourceSpec(**data)


@pytest.fixture
def habr_ds_spec() -> RSSFeedSourceSpec:
    path = Path(__file__).parent.parent.parent / "fixtures" / "specs" / "rss_habr_ds.yaml"
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return RSSFeedSourceSpec(**data)


@pytest.mark.network
@pytest.mark.asyncio
async def test_habr_ml_rss_returns_items(
    habr_ml_spec: RSSFeedSourceSpec, in_memory_store, null_auth
) -> None:
    source = RSSFeedSource(spec=habr_ml_spec, auth=null_auth, store=in_memory_store)

    # Timeout 20s as per plan
    items = []
    async with asyncio.timeout(20):
        async for item in source.fetch():
            items.append(item)
            if len(items) >= 30:
                break

    assert len(items) >= 1
    assert all(isinstance(item, RawItem) for item in items)
    assert all(item.url is not None for item in items)
    assert all(item.external_id for item in items)

    # Uniqueness check
    ext_ids = [item.external_id for item in items]
    assert len(ext_ids) == len(set(ext_ids))


@pytest.mark.network
@pytest.mark.asyncio
async def test_habr_ds_rss_returns_items(
    habr_ds_spec: RSSFeedSourceSpec, in_memory_store, null_auth
) -> None:
    source = RSSFeedSource(spec=habr_ds_spec, auth=null_auth, store=in_memory_store)

    items = []
    async with asyncio.timeout(20):
        async for item in source.fetch():
            items.append(item)
            if len(items) >= 30:
                break

    assert len(items) >= 1


@pytest.mark.network
@pytest.mark.asyncio
async def test_habr_rss_incremental_dedup(
    habr_ml_spec: RSSFeedSourceSpec, in_memory_store, null_auth
) -> None:
    source = RSSFeedSource(spec=habr_ml_spec, auth=null_auth, store=in_memory_store)

    # First fetch
    first_items = []
    async for item in source.fetch():
        first_items.append(item)

    assert len(first_items) >= 1

    # Second fetch should return 0 new items because they are already in store
    second_items = []
    async for item in source.fetch():
        second_items.append(item)

    assert len(second_items) == 0

    # Check run state
    state = in_memory_store.get_run_state(f"rss_feed:{habr_ml_spec.source_name}:seen_ids")
    assert state is not None
    assert len(state) >= len(first_items)


@pytest.mark.asyncio
async def test_habr_rss_fixture_roundtrip(
    habr_ml_spec: RSSFeedSourceSpec, in_memory_store, habr_ml_rss_xml: str, monkeypatch, null_auth
) -> None:
    # Mock httpx.AsyncClient.get
    class MockResponse:
        def __init__(self, text: str):
            self.text = text
            self.status_code = 200

        def raise_for_status(self):
            pass

    async def mock_get(*args, **kwargs):
        return MockResponse(habr_ml_rss_xml)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    source = RSSFeedSource(spec=habr_ml_spec, auth=null_auth, store=in_memory_store)

    items = []
    async for item in source.fetch():
        items.append(item)

    assert len(items) == 5
    assert any("ML Engineer" in item.text for item in items)
    assert all(item.text for item in items)
