"""Telegram posting sink factory — registered here to allow adapter + infra imports."""

from __future__ import annotations

from typing import cast

from job_ftch.application.registry import register_sink
from job_ftch.config import Settings
from job_ftch.sinks.telegram_posting import TelegramPostingClientLike, TelegramPostingSink


def _build_telegram_client(settings: Settings) -> TelegramPostingClientLike:
    if settings.telegram_api_id is None or settings.telegram_api_hash is None:
        msg = "Telegram posting requires JOB_FTCH_TELEGRAM_API_ID and JOB_FTCH_TELEGRAM_API_HASH."
        raise ValueError(msg)
    from telethon import TelegramClient

    settings.telegram_session_path.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(
        str(settings.telegram_session_path),
        settings.telegram_api_id,
        settings.telegram_api_hash.get_secret_value(),
    )
    client.flood_sleep_threshold = settings.telegram_flood_sleep_threshold_seconds
    return cast("TelegramPostingClientLike", client)


@register_sink("telegram_posting")
def _build_telegram_posting_sink(settings: Settings) -> TelegramPostingSink:
    if settings.telegram_publish_entity is None:
        msg = "telegram_publish_entity is required when sink_backend=telegram_posting."
        raise ValueError(msg)
    from job_ftch.adapters.telegram_bot.formatter import format_job_digest  # lazy: only in bot mode

    return TelegramPostingSink(
        _build_telegram_client(settings),
        settings.telegram_publish_entity,
        own_client=True,
        notify_mode=settings.notify_mode,
        notify_batch_size=settings.notify_batch_size,
        digest_formatter=lambda jobs, page, page_size: format_job_digest(
            jobs,
            page=page,
            page_size=page_size,
        ),
    )
