"""Main entry point for the Telegram bot using aiogram 3.x."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from adapters.telegram_bot.handlers import admin, base, profiles, search_digest, upload
from adapters.telegram_bot.middlewares.auth import AuthMiddleware
from adapters.telegram_bot.middlewares.di import DIMiddleware
from adapters.telegram_bot.middlewares.throttling import ThrottlingMiddleware

if TYPE_CHECKING:
    from adapters.telegram_bot.config import TelegramBotConfig
    from job_ftch.application.contracts import CrossEncoderPort, EmbeddingProvider
    from job_ftch.application.tenant_runner import TenantRunner

logger = structlog.get_logger(__name__)


def build_bot(config: TelegramBotConfig) -> Bot:
    """Build aiogram Bot instance."""
    return Bot(token=config.token)


def build_dispatcher(
    runner: TenantRunner,
    config: TelegramBotConfig,
    embedding_provider: EmbeddingProvider | None = None,
    reranker: CrossEncoderPort | None = None,
) -> Dispatcher:
    """Build aiogram Dispatcher instance with all routers and middlewares."""
    dp = Dispatcher(storage=MemoryStorage())

    # Register middlewares (order matters)
    # 1. DI middleware (injects runner, config, etc.)
    dp.update.outer_middleware(
        DIMiddleware(
            runner=runner,
            config=config,
            embedding_provider=embedding_provider,
            reranker=reranker,
        )
    )
    # 2. Auth middleware (checks if user is allowed)
    dp.update.outer_middleware(AuthMiddleware())
    # 3. Throttling middleware (only for messages)
    dp.message.middleware(ThrottlingMiddleware())

    # Register routers
    dp.include_router(base.router)
    dp.include_router(admin.router)
    dp.include_router(profiles.router)
    dp.include_router(search_digest.router)
    dp.include_router(upload.router)

    return dp


async def start_polling(
    runner: TenantRunner,
    config: TelegramBotConfig,
    embedding_provider: EmbeddingProvider | None = None,
    reranker: CrossEncoderPort | None = None,
) -> None:
    """Start polling for updates."""
    bot = build_bot(config)
    dp = build_dispatcher(
        runner=runner,
        config=config,
        embedding_provider=embedding_provider,
        reranker=reranker,
    )

    logger.info("telegram_bot_polling_started")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
