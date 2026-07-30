"""Shared utilities for Telegram bot handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from aiogram.types import CallbackQuery, Message

    from job_ftch.adapters.telegram_bot.config import TelegramBotConfig

logger = structlog.get_logger(__name__)


async def safe_error_reply(message: Message, exc: Exception, context: str) -> None:
    """Log the real exception and reply with a generic user-safe message."""
    logger.exception(context, error=str(exc))
    await message.answer("❌ Произошла ошибка. Попробуйте позже.")


def is_admin_user(config: TelegramBotConfig, user_id: int | None) -> bool:
    return user_id is not None and user_id in config.admin_user_ids


async def require_admin_message(
    message: Message,
    config: TelegramBotConfig,
    user_id_override: int | None = None,
) -> bool:
    user_id = (
        user_id_override
        if user_id_override is not None
        else (message.from_user.id if message.from_user else None)
    )
    if is_admin_user(config, user_id):
        return True
    await message.answer("Команда доступна только администратору бота.")
    return False


async def require_admin_callback(callback: CallbackQuery, config: TelegramBotConfig) -> bool:
    user_id = callback.from_user.id if callback.from_user else None
    if is_admin_user(config, user_id):
        return True
    await callback.answer("Доступно только администратору бота.", show_alert=True)
    return False
