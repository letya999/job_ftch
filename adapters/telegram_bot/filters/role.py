"""Filters for the Telegram bot."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram.filters import BaseFilter

if TYPE_CHECKING:
    from aiogram.types import Message

    from adapters.telegram_bot.config import TelegramBotConfig


class IsAdminFilter(BaseFilter):
    """Filter to check if user is an admin."""

    async def __call__(self, message: Message, config: TelegramBotConfig) -> bool:
        if not message.from_user:
            return False
        return message.from_user.id in config.admin_user_ids
