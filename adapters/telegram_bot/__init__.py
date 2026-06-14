"""Telegram bot adapter using aiogram 3.x."""

from adapters.telegram_bot.api import create_app
from adapters.telegram_bot.config import TelegramBotConfig, load_bot_config
from adapters.telegram_bot.formatter import format_job_digest, format_job_message
from adapters.telegram_bot.main import build_bot, build_dispatcher, start_polling

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
