from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import TypeAdapter

from job_ftch.domain import RawItem
from job_ftch.domain.source_spec import RSSFeedSourceSpec, SourceSpec

feedparser = pytest.importorskip("feedparser")

SAMPLE_FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Jobs</title>
    <item>
      <title>LLM Engineer</title>
      <link>https://example.com/jobs/1</link>
      <description>Build LLMs</description>
      <guid>job-1</guid>
    </item>
    <item>
      <title>MLOps Engineer</title>
      <link>https://example.com/jobs/2</link>
      <description>Run MLOps</description>
      <guid>job-2</guid>
    </item>
  </channel>
</rss>"""

SAMPLE_FEED_XML_WITH_DATES = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Jobs</title>
    <item>
      <title>Fresh role</title>
      <link>https://example.com/jobs/1</link>
      <description>Fresh</description>
      <guid>job-1</guid>
      <pubDate>Sat, 27 Jun 2026 10:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Old role</title>
      <link>https://example.com/jobs/2</link>
      <description>Old</description>
      <guid>job-2</guid>
      <pubDate>Sat, 20 Jun 2026 10:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""


def _mock_httpx_client(response_text: str):
    """Return a patched httpx.AsyncClient that serves fixed XML."""
    mock_response = MagicMock()
    mock_response.text = response_text
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)
    return mock_client


def test_rss_feed_spec_roundtrip():
    raw = {"type": "rss_feed", "feed_url": "https://example.com/feed.xml"}
    adapter = TypeAdapter(SourceSpec)
    spec = adapter.validate_python(raw)
    assert isinstance(spec, RSSFeedSourceSpec)
    assert spec.incremental is True


@pytest.mark.asyncio
async def test_rss_source_yields_items():
    from job_ftch.infrastructure.sources.realtime.rss import RSSFeedSource

    spec = RSSFeedSourceSpec(feed_url="https://example.com/feed.xml")
    auth = MagicMock()
    store = MagicMock()
    store.get = AsyncMock(return_value=None)
    store.set = AsyncMock()

    source = RSSFeedSource(spec, auth, store)

    with patch("httpx.AsyncClient", return_value=_mock_httpx_client(SAMPLE_FEED_XML)):
        items = [item async for item in source.fetch()]

    assert len(items) == 2
    assert isinstance(items[0], RawItem)
    assert "LLM Engineer" in items[0].text
    assert items[0].external_id == "job-1"
    assert items[1].external_id == "job-2"
    assert "MLOps Engineer" in items[1].text
    store.get.assert_awaited_once()
    store.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_rss_source_incremental_dedup():
    """Second run with same IDs yields 0 items."""
    from job_ftch.infrastructure.sources.realtime.rss import RSSFeedSource

    spec = RSSFeedSourceSpec(feed_url="https://example.com/feed.xml", incremental=True)
    auth = MagicMock()
    store = MagicMock()
    store.get = AsyncMock(return_value="job-1,job-2")
    store.set = AsyncMock()

    source = RSSFeedSource(spec, auth, store)

    with patch("httpx.AsyncClient", return_value=_mock_httpx_client(SAMPLE_FEED_XML)):
        items = [item async for item in source.fetch()]

    assert items == []
    store.get.assert_awaited_once()
    store.set.assert_not_called()


@pytest.mark.asyncio
async def test_rss_source_filters_items_newer_than_cutoff():
    from job_ftch.infrastructure.sources.realtime.rss import RSSFeedSource

    spec = RSSFeedSourceSpec(
        feed_url="https://example.com/feed.xml",
        freshness_cutoff_utc=datetime(2026, 6, 27, 0, 0, tzinfo=UTC),
    )
    auth = MagicMock()
    store = MagicMock()
    store.get = AsyncMock(return_value=None)
    store.set = AsyncMock()

    source = RSSFeedSource(spec, auth, store)

    with patch(
        "httpx.AsyncClient",
        return_value=_mock_httpx_client(SAMPLE_FEED_XML_WITH_DATES),
    ):
        items = [item async for item in source.fetch()]

    assert [item.external_id for item in items] == ["job-1"]


def test_telegram_realtime_spec_roundtrip():
    from job_ftch.domain.source_spec import TelegramRealtimeSourceSpec

    raw = {"type": "telegram_realtime", "entity": "@ai_jobs_ru"}
    adapter = TypeAdapter(SourceSpec)
    spec = adapter.validate_python(raw)
    assert isinstance(spec, TelegramRealtimeSourceSpec)
    assert spec.entity == "@ai_jobs_ru"


def test_webhook_spec_roundtrip():
    from job_ftch.domain.source_spec import WebhookSourceSpec

    raw = {"type": "webhook", "path": "/ingest/jobs"}
    adapter = TypeAdapter(SourceSpec)
    spec = adapter.validate_python(raw)
    assert isinstance(spec, WebhookSourceSpec)
    assert spec.path == "/ingest/jobs"


def test_websocket_spec_roundtrip():
    from job_ftch.domain.source_spec import WebSocketSourceSpec

    raw = {"type": "websocket", "url": "wss://example.com/stream"}
    adapter = TypeAdapter(SourceSpec)
    spec = adapter.validate_python(raw)
    assert isinstance(spec, WebSocketSourceSpec)


def test_webhook_source_requires_aiohttp_dep():
    from unittest.mock import MagicMock, patch

    from job_ftch.domain.source_spec import WebhookSourceSpec
    from job_ftch.infrastructure.sources.realtime.webhook import WebhookSource

    spec = WebhookSourceSpec(path="/test")
    with (
        patch("job_ftch.infrastructure.sources.realtime.webhook._AIOHTTP_AVAILABLE", False),
        pytest.raises(ImportError, match="aiohttp"),
    ):
        WebhookSource(spec, MagicMock())


def test_websocket_source_requires_websockets_dep():
    from unittest.mock import MagicMock, patch

    from job_ftch.domain.source_spec import WebSocketSourceSpec
    from job_ftch.infrastructure.sources.realtime.websocket import WebSocketSource

    spec = WebSocketSourceSpec(url="wss://example.com/stream")
    with (
        patch("job_ftch.infrastructure.sources.realtime.websocket._WEBSOCKETS_AVAILABLE", False),
        pytest.raises(ImportError, match="websockets"),
    ):
        WebSocketSource(spec, MagicMock())
