"""Dependency injection middleware for the Telegram bot."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aiogram import BaseMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from aiogram.types import TelegramObject

    from job_ftch.adapters.telegram_bot.config import TelegramBotConfig
    from job_ftch.application.tenant_runner import TenantRunner


class DIMiddleware(BaseMiddleware):
    """Middleware for injecting dependencies into handlers."""

    def __init__(
        self,
        runner: TenantRunner,
        config: TelegramBotConfig,
    ) -> None:
        self._runner = runner
        self._config = config

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["runner"] = self._runner
        data["config"] = self._config
        return await handler(event, data)
