from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("telethon", reason="telethon not installed")
import yaml

from job_ftch.domain.source_spec import TelegramChannelSpec
from job_ftch.infrastructure.sources.telegram import TelegramChannelSource


@pytest.fixture
def tg_getmatch_spec() -> TelegramChannelSpec:
    path = Path(__file__).parent.parent.parent / "fixtures" / "specs" / "telegram_getmatch.yaml"
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return TelegramChannelSpec(**data)


@pytest.fixture
def tg_habr_spec() -> TelegramChannelSpec:
    path = Path(__file__).parent.parent.parent / "fixtures" / "specs" / "telegram_habr_career.yaml"
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return TelegramChannelSpec(**data)


@pytest.mark.anyio
async def test_telegram_fixture_unit(
    tg_getmatch_spec: TelegramChannelSpec, tg_messages_json: list, monkeypatch
) -> None:
    # Mock Telethon client
    mock_client = MagicMock()

    # Create mock messages that look like Telethon messages
    mock_messages = []
    for msg_data in tg_messages_json:
        m = MagicMock()
        m.id = msg_data["id"]
        m.message = msg_data["message"]
        from datetime import datetime

        m.date = datetime.fromisoformat(msg_data["date"])
        m.peer_id = MagicMock()
        m.peer_id.channel_id = msg_data["peer_id"]["channel_id"]
        mock_messages.append(m)

    async def mock_get_messages(*args, **kwargs):
        return mock_messages

    mock_client.get_messages = mock_get_messages
    mock_client.get_entity = AsyncMock(return_value=MagicMock())

    # TelegramChannelSource takes client, channel, limit, etc. in __init__
    source = TelegramChannelSource(
        client=mock_client, channel=tg_getmatch_spec.entity, limit=tg_getmatch_spec.limit
    )

    items = []
    async for item in source.fetch():
        items.append(item)

    assert len(items) == 5
    assert all(item.text for item in items)
    assert all(item.source_name == "getmatch" for item in items)  # normalized handle


@pytest.mark.telegram
@pytest.mark.anyio
async def test_telegram_getmatch_live(tg_getmatch_spec: TelegramChannelSpec) -> None:
    api_id = os.environ.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH")

    if not all([api_id, api_hash]):
        pytest.skip("Telegram credentials not set")

    # Real Telethon client
    from telethon import TelegramClient

    client = TelegramClient("session_test", int(api_id), api_hash)

    source = TelegramChannelSource(
        client=client, channel=tg_getmatch_spec.entity, limit=10, own_client=True
    )

    items = []
    async with asyncio.timeout(20):
        async for item in source.fetch():
            items.append(item)
            if len(items) >= 5:
                break

    assert len(items) >= 1
    await asyncio.sleep(1.5)


@pytest.mark.telegram
@pytest.mark.anyio
async def test_telegram_habr_career_live(tg_habr_spec: TelegramChannelSpec) -> None:
    api_id = os.environ.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH")

    if not all([api_id, api_hash]):
        pytest.skip("Telegram credentials not set")

    from telethon import TelegramClient

    client = TelegramClient("session_test_2", int(api_id), api_hash)

    source = TelegramChannelSource(
        client=client, channel=tg_habr_spec.entity, limit=10, own_client=True
    )

    items = []
    async with asyncio.timeout(20):
        async for item in source.fetch():
            items.append(item)
            if len(items) >= 5:
                break

    assert len(items) >= 1
    await asyncio.sleep(1.5)
