"""Telegram bot adapter using aiogram 3.x."""

from job_ftch.adapters.telegram_bot.config import TelegramBotConfig, load_bot_config
from job_ftch.adapters.telegram_bot.formatter import format_job_digest, format_job_message
from job_ftch.adapters.telegram_bot.main import build_bot, build_dispatcher, start_polling

__all__ = [
    "TelegramBotConfig",
    "build_bot",
    "build_dispatcher",
    "create_app",
    "format_job_digest",
    "format_job_message",
    "load_bot_config",
    "start_polling",
]


def __getattr__(name: str) -> object:
    if name == "create_app":
        from job_ftch.adapters.telegram_bot.api import create_app as _create_app

        return _create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
