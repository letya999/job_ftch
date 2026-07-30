"""Throttling middleware for the Telegram bot."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from aiogram import BaseMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from aiogram.types import TelegramObject

    from job_ftch.adapters.telegram_bot.config import TelegramBotConfig


# Entries newer than this cannot throttle anything, so keeping them only grows
# the process. Matters most with open_access=True, where any user id can appear.
_ENTRY_TTL_SECONDS = 3600.0
_MAX_TRACKED_USERS = 10_000


class ThrottlingMiddleware(BaseMiddleware):
    """Middleware to prevent spamming."""

    def __init__(self) -> None:
        self._last_seen: dict[int, float] = {}

    def _evict_expired(self, now: float) -> None:
        if len(self._last_seen) < _MAX_TRACKED_USERS:
            return
        cutoff = now - _ENTRY_TTL_SECONDS
        self._last_seen = {
            user_id: seen for user_id, seen in self._last_seen.items() if seen > cutoff
        }
        if len(self._last_seen) >= _MAX_TRACKED_USERS:
            # Everything is recent (a burst, not a leak): drop the newest half
            # so the map stays bounded regardless of traffic shape.
            ordered = sorted(self._last_seen.items(), key=lambda entry: entry[1], reverse=True)
            self._last_seen = dict(ordered[: _MAX_TRACKED_USERS // 2])

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Only throttle messages
        from aiogram.types import Message

        if not isinstance(event, Message):
            return await handler(event, data)

        config: TelegramBotConfig = data["config"]
        user_id = event.from_user.id if event.from_user else None

        if user_id is None or user_id in config.admin_user_ids:
            return await handler(event, data)

        now = time.time()
        last_time = self._last_seen.get(user_id, 0.0)

        if now - last_time < config.rate_limit_seconds:
            await event.answer("Too many requests. Please wait.")
            return None

        self._evict_expired(now)
        self._last_seen[user_id] = now
        return await handler(event, data)
