"""Outbound Telegram posting sink."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Protocol, cast

from job_ftch.application.registry import register_sink

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.config import Settings
    from job_ftch.domain import Job


class TelegramPostingClientLike(Protocol):
    async def send_message(self, entity: object, message: str, **kwargs: object) -> object:
        """Send a message to a Telegram entity."""


@asynccontextmanager
async def _client_session(
    client: TelegramPostingClientLike,
    *,
    own_client: bool,
) -> AsyncIterator[TelegramPostingClientLike]:
    if own_client:
        async with client as managed_client:  # type: ignore[attr-defined]
            yield managed_client
        return
    yield client


def _format_job(job: Job) -> str:
    parts = [job.title or "Untitled AI job"]
    if job.company:
        parts.append(f"Company: {job.company}")
    if job.location:
        parts.append(f"Location: {job.location}")
    parts.append(f"Work mode: {job.work_mode.value}")
    if job.compensation is not None:
        parts.append(
            f"Compensation: {job.compensation.currency} "
            f"{job.compensation.min_amount or '?'} - {job.compensation.max_amount or '?'}"
        )
    if job.canonical_url is not None:
        parts.append(f"URL: {job.canonical_url}")
    parts.append("")
    parts.append(job.description)
    return "\n".join(parts)


class TelegramPostingSink:
    def __init__(
        self,
        client: TelegramPostingClientLike,
        entity: str,
        *,
        own_client: bool = False,
        notify_mode: str = "instant",
        notify_batch_size: int = 10,
    ) -> None:
        self._client = client
        self._entity = entity
        self._own_client = own_client
        self._notify_mode = notify_mode
        self._notify_batch_size = notify_batch_size
        self._pending_jobs: list[Job] = []

    async def emit(self, item: Job) -> None:
        if self._notify_mode == "instant":
            async with _client_session(self._client, own_client=self._own_client) as client:
                await client.send_message(self._entity, _format_job(item), link_preview=False)
        else:
            self._pending_jobs.append(item)

    async def flush(self) -> None:
        if not self._pending_jobs:
            return
        from job_ftch.adapters.telegram_bot.formatter import format_job_digest

        # Split into chunks to avoid message length limits
        chunk_size = self._notify_batch_size
        async with _client_session(self._client, own_client=self._own_client) as client:
            for i in range(0, len(self._pending_jobs), chunk_size):
                chunk = self._pending_jobs[i : i + chunk_size]
                header = f"<b>Job Digest ({i + 1}-{i + len(chunk)})</b>\n\n"
                digest = format_job_digest(chunk, page=0, page_size=chunk_size)
                await client.send_message(self._entity, header + digest, link_preview=False)
        self._pending_jobs.clear()


def _build_telegram_client(settings: Settings) -> TelegramPostingClientLike:
    if settings.telegram_api_id is None or settings.telegram_api_hash is None:
        msg = "Telegram posting requires JOB_FTCH_TELEGRAM_API_ID and JOB_FTCH_TELEGRAM_API_HASH."
        raise ValueError(msg)
    from telethon import TelegramClient

    settings.telegram_session_path.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(
        str(settings.telegram_session_path),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    client.flood_sleep_threshold = settings.telegram_flood_sleep_threshold_seconds
    return cast("TelegramPostingClientLike", client)


@register_sink("telegram_posting")
def _build_telegram_posting_sink(settings: Settings) -> TelegramPostingSink:
    if settings.telegram_publish_entity is None:
        msg = "telegram_publish_entity is required when sink_backend=telegram_posting."
        raise ValueError(msg)
    return TelegramPostingSink(
        _build_telegram_client(settings),
        settings.telegram_publish_entity,
        own_client=True,
        notify_mode=settings.notify_mode,
        notify_batch_size=settings.notify_batch_size,
    )
