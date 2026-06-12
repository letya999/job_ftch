"""Real-time Telegram source using Telethon event handlers."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from job_ftch.application.registry import register_source_spec
from job_ftch.domain import RawItem, SourceKind

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.application.contracts import AuthProvider
    from job_ftch.domain import QuarantinedRawItem
    from job_ftch.domain.source_spec import TelegramRealtimeSourceSpec

logger = structlog.get_logger(__name__)

try:
    from telethon import TelegramClient, events

    _TELETHON_AVAILABLE = True
except ImportError:
    TelegramClient = None  # type: ignore[assignment,misc]
    events = None  # type: ignore[assignment]
    _TELETHON_AVAILABLE = False


class TelegramRealtimeSource:
    """
    Infinite async generator: yields RawItem as new Telegram messages arrive.
    Graceful shutdown: stop_event set → unregisters handlers → returns.
    """

    def __init__(
        self,
        spec: TelegramRealtimeSourceSpec,
        auth: AuthProvider,
    ) -> None:
        self.spec = spec
        self.auth = auth
        self.source_name = spec.source_name or spec.entity
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    async def fetch(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        if not _TELETHON_AVAILABLE:
            raise ImportError("telethon is required for telegram_realtime sources.")

        creds = self.auth.resolve(self.spec.auth_source_id or "telegram")
        api_id = int(creds.get("api_id", 0))
        api_hash = creds.get("api_hash", "")

        queue: asyncio.Queue[RawItem] = asyncio.Queue()

        async with TelegramClient("session", api_id, api_hash) as client:

            @client.on(events.NewMessage(chats=[self.spec.entity]))  # type: ignore[untyped-decorator]
            async def handler(event: Any) -> None:
                msg = event.message
                item = RawItem(
                    source_kind=SourceKind.TELEGRAM_CHANNEL,
                    source_name=self.source_name,
                    external_id=str(msg.id),
                    url=None,
                    text=msg.text or "",
                    metadata={"chat": self.spec.entity, "msg_id": msg.id},
                )
                await queue.put(item)

            await client.start()
            logger.info("telegram_realtime_started", entity=self.spec.entity)

            while not self._stop_event.is_set():
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield item
                except TimeoutError:
                    continue


@register_source_spec("telegram_realtime")
def _create_realtime(spec: Any, auth: AuthProvider, store: Any = None) -> TelegramRealtimeSource:
    return TelegramRealtimeSource(spec, auth)
