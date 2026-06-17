"""Main entry point for the Telegram bot using aiogram 3.x."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from adapters.telegram_bot.handlers import base, examples, pipeline, sources
from adapters.telegram_bot.middlewares.auth import AuthMiddleware
from adapters.telegram_bot.middlewares.di import DIMiddleware
from adapters.telegram_bot.middlewares.throttling import ThrottlingMiddleware

if TYPE_CHECKING:
    from adapters.telegram_bot.config import TelegramBotConfig
    from job_ftch.application.contracts import CrossEncoderPort, EmbeddingProvider
    from job_ftch.application.tenant_runner import TenantRunner

logger = structlog.get_logger(__name__)


def _build_bot_commands() -> list[BotCommand]:
    return [
        BotCommand(command="start", description="Главное меню / Статус"),
        BotCommand(command="positive", description="Добавить подходящее резюме"),
        BotCommand(command="negative", description="Добавить НЕ подходящее резюме"),
        BotCommand(command="examples", description="Список моих примеров"),
        BotCommand(command="sources", description="Список источников (URL)"),
        BotCommand(command="run", description="Запустить поиск сейчас"),
        BotCommand(
            command="clear", description="Очистить историю — следующий запуск увидит всё заново"
        ),
    ]


async def configure_bot(bot: Bot, config: TelegramBotConfig | None = None) -> None:
    """Apply Bot API configuration needed at startup."""
    commands = _build_bot_commands()
    await bot.set_my_commands(commands)
    logger.info(
        "telegram_bot_commands_registered",
        command_count=len(commands),
        commands=[command.command for command in commands],
    )


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
    dp.include_router(examples.router)
    dp.include_router(sources.router)
    dp.include_router(pipeline.router)

    return dp


async def start_polling(
    runner: TenantRunner,
    config: TelegramBotConfig,
    embedding_provider: EmbeddingProvider | None = None,
    reranker: CrossEncoderPort | None = None,
) -> None:
    """Start polling for updates."""
    bot = build_bot(config)
    await configure_bot(bot, config)
    dp = build_dispatcher(
        runner=runner,
        config=config,
        embedding_provider=embedding_provider,
        reranker=reranker,
    )

    logger.info("telegram_bot_polling_started")
    try:
        await dp.start_polling(bot, drop_pending_updates=True)
    finally:
        await bot.session.close()
