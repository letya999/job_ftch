"""Throttling middleware for the Telegram bot."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from aiogram import BaseMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from aiogram.types import TelegramObject

    from adapters.telegram_bot.config import TelegramBotConfig


class ThrottlingMiddleware(BaseMiddleware):
    """Middleware to prevent spamming."""

    def __init__(self) -> None:
        self._last_seen: dict[int, float] = {}

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

        self._last_seen[user_id] = now
        return await handler(event, data)
