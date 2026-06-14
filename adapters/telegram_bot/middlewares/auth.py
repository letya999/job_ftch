"""Authentication middleware for the Telegram bot."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aiogram import BaseMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from aiogram.types import TelegramObject

    from adapters.telegram_bot.config import TelegramBotConfig


class AuthMiddleware(BaseMiddleware):
    """Middleware for user/chat authorization."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        config: TelegramBotConfig = data["config"]

        if config.open_access:
            return await handler(event, data)

        user = data.get("event_from_user")
        chat = data.get("event_chat")

        user_id = user.id if user else None
        chat_id = chat.id if chat else None

        # Admins always pass regardless of other allowlists
        if user_id is not None and user_id in config.admin_user_ids:
            return await handler(event, data)

        # Secure by default: deny all if no allowlist is configured (and not open_access)
        if not config.allowed_user_ids and not config.allowed_chat_ids:
            return None

        # AND semantics: both gates apply when both lists are populated
        user_ok = (not config.allowed_user_ids) or (user_id in config.allowed_user_ids)
        chat_ok = (not config.allowed_chat_ids) or (chat_id in config.allowed_chat_ids)

        if not (user_ok and chat_ok):
            # Silent drop to prevent enumeration
            return None

        return await handler(event, data)
