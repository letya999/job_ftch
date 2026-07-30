"""Outbound Telegram posting sink."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

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
    # Use the LLM-formatted ``presentable`` field when PresentableTextNode
    # has run (per ADR-019 point ③). Otherwise fall back to the basic format.
    presentable = getattr(job, "presentable", None)
    if presentable is not None:
        lines: list[str] = []
        title = getattr(presentable, "title", None) or job.title or "Untitled AI job"
        lines.append(f"<b>{title}</b>")
        if getattr(presentable, "location_formatted", None):
            lines.append(f"📍 {presentable.location_formatted}")
        if getattr(presentable, "salary_formatted", None):
            lines.append(f"💰 {presentable.salary_formatted}")
        if getattr(presentable, "body", None):
            lines.append("")
            lines.append(presentable.body)
        if getattr(presentable, "contact_section", None):
            lines.append("")
            lines.append(presentable.contact_section)
        if job.canonical_url is not None:
            lines.append(f"\n🔗 {job.canonical_url}")
        if getattr(presentable, "tags", None):
            lines.append("\n" + " ".join(f"#{t}" for t in presentable.tags))
        return "\n".join(lines)

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
    parts.append(job.description or "")
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
        digest_formatter: Callable[[list[Job], int, int], str] | None = None,
    ) -> None:
        self._client = client
        self._entity = entity
        self._own_client = own_client
        self._notify_mode = notify_mode
        self._notify_batch_size = notify_batch_size
        self._digest_formatter = digest_formatter
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

        # Split into chunks to avoid message length limits
        chunk_size = self._notify_batch_size
        async with _client_session(self._client, own_client=self._own_client) as client:
            for i in range(0, len(self._pending_jobs), chunk_size):
                chunk = self._pending_jobs[i : i + chunk_size]
                header = f"<b>Job Digest ({i + 1}-{i + len(chunk)})</b>\n\n"

                if self._digest_formatter is None:
                    # fallback: minimal plain-text digest with no adapter dep
                    digest = "\n\n".join(
                        f"<b>{j.title or '?'}</b> — {j.company or '?'}" for j in chunk
                    )
                else:
                    digest = self._digest_formatter(chunk, 0, chunk_size)

                await client.send_message(self._entity, header + digest, link_preview=False)
        self._pending_jobs.clear()
