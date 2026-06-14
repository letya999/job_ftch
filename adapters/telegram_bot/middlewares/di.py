"""Dependency injection middleware for the Telegram bot."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aiogram import BaseMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from aiogram.types import TelegramObject

    from adapters.telegram_bot.config import TelegramBotConfig
    from job_ftch.application.contracts import CrossEncoderPort, EmbeddingProvider
    from job_ftch.application.tenant_runner import TenantRunner


class DIMiddleware(BaseMiddleware):
    """Middleware for injecting dependencies into handlers."""

    def __init__(
        self,
        runner: TenantRunner,
        config: TelegramBotConfig,
        embedding_provider: EmbeddingProvider | None = None,
        reranker: CrossEncoderPort | None = None,
    ) -> None:
        self._runner = runner
        self._config = config
        self._embedding_provider = embedding_provider
        self._reranker = reranker

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["runner"] = self._runner
        data["config"] = self._config
        data["embedding_provider"] = self._embedding_provider
        data["reranker"] = self._reranker
        return await handler(event, data)
