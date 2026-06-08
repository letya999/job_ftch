from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import TypeAdapter

from domain import RawItem
from domain.source_spec import RSSFeedSourceSpec, SourceSpec

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


def test_rss_feed_spec_roundtrip():
    raw = {"type": "rss_feed", "feed_url": "https://example.com/feed.xml"}
    adapter = TypeAdapter(SourceSpec)
    spec = adapter.validate_python(raw)
    assert isinstance(spec, RSSFeedSourceSpec)
    assert spec.incremental is True


@pytest.mark.asyncio
async def test_rss_source_yields_items():
    from infrastructure.sources.realtime.rss import RSSFeedSource

    spec = RSSFeedSourceSpec(feed_url="https://example.com/feed.xml")
    auth = MagicMock()
    store = MagicMock()
    store.get_run_state = AsyncMock(return_value=None)
    store.set_run_state = AsyncMock()

    # Mock feedparser response
    mock_feed = MagicMock()
    mock_feed.entries = [
        {
            "title": "LLM Engineer",
            "link": "https://example.com/jobs/1",
            "description": "Build LLMs",
            "id": "job-1",
        },
        {
            "title": "MLOps Engineer",
            "link": "https://example.com/jobs/2",
            "description": "Run MLOps",
            "id": "job-2",
        },
    ]

    with (
        patch("infrastructure.sources.realtime.rss._FEEDPARSER_AVAILABLE", True),
        patch("infrastructure.sources.realtime.rss.feedparser") as mock_fp,
    ):
        mock_fp.parse.return_value = mock_feed

        source = RSSFeedSource(spec, auth, store)

        mock_response = MagicMock()
        mock_response.text = SAMPLE_FEED_XML
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            items = [item async for item in source.fetch()]

    assert len(items) == 2
    assert isinstance(items[0], RawItem)
    assert "LLM Engineer" in items[0].text


@pytest.mark.asyncio
async def test_rss_source_incremental_dedup():
    """Second run with same IDs yields 0 items."""
    from infrastructure.sources.realtime.rss import RSSFeedSource

    spec = RSSFeedSourceSpec(feed_url="https://example.com/feed.xml", incremental=True)
    auth = MagicMock()
    store = MagicMock()
    # Simulate both IDs already seen
    store.get_run_state = AsyncMock(return_value="job-1,job-2")
    store.set_run_state = AsyncMock()

    # Mock feedparser response
    mock_feed = MagicMock()
    mock_feed.entries = [
        {
            "title": "LLM Engineer",
            "link": "https://example.com/jobs/1",
            "description": "Build LLMs",
            "id": "job-1",
        },
        {
            "title": "MLOps Engineer",
            "link": "https://example.com/jobs/2",
            "description": "Run MLOps",
            "id": "job-2",
        },
    ]

    with (
        patch("infrastructure.sources.realtime.rss._FEEDPARSER_AVAILABLE", True),
        patch("infrastructure.sources.realtime.rss.feedparser") as mock_fp,
    ):
        mock_fp.parse.return_value = mock_feed

        source = RSSFeedSource(spec, auth, store)

        mock_response = MagicMock()
        mock_response.text = SAMPLE_FEED_XML
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            items = [item async for item in source.fetch()]

    assert items == []


def test_telegram_realtime_spec_roundtrip():
    from domain.source_spec import TelegramRealtimeSourceSpec

    raw = {"type": "telegram_realtime", "entity": "@ai_jobs_ru"}
    adapter = TypeAdapter(SourceSpec)
    spec = adapter.validate_python(raw)
    assert isinstance(spec, TelegramRealtimeSourceSpec)
    assert spec.entity == "@ai_jobs_ru"


def test_webhook_spec_roundtrip():
    from domain.source_spec import WebhookSourceSpec

    raw = {"type": "webhook", "path": "/ingest/jobs"}
    adapter = TypeAdapter(SourceSpec)
    spec = adapter.validate_python(raw)
    assert isinstance(spec, WebhookSourceSpec)
    assert spec.path == "/ingest/jobs"


def test_websocket_spec_roundtrip():
    from domain.source_spec import WebSocketSourceSpec

    raw = {"type": "websocket", "url": "wss://example.com/stream"}
    adapter = TypeAdapter(SourceSpec)
    spec = adapter.validate_python(raw)
    assert isinstance(spec, WebSocketSourceSpec)


def test_webhook_source_raises_not_implemented():
    import asyncio
    from unittest.mock import MagicMock

    from domain.source_spec import WebhookSourceSpec
    from infrastructure.sources.realtime.webhook import WebhookSource

    spec = WebhookSourceSpec(path="/test")
    src = WebhookSource(spec, MagicMock())
    with pytest.raises(NotImplementedError):
        asyncio.run(_drain(src))


def test_websocket_source_raises_not_implemented():
    import asyncio
    from unittest.mock import MagicMock

    from domain.source_spec import WebSocketSourceSpec
    from infrastructure.sources.realtime.websocket import WebSocketSource

    spec = WebSocketSourceSpec(url="wss://example.com/stream")
    src = WebSocketSource(spec, MagicMock())
    with pytest.raises(NotImplementedError):
        asyncio.run(_drain(src))


async def _drain(src):
    async for _ in src.fetch():
        pass
