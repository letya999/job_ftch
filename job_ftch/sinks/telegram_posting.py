"""Outbound Telegram posting sink."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Protocol

from job_ftch.publication.card import build_card
from job_ftch.publication.layout import CardLayout, load_layout
from job_ftch.publication.render import render_card
from job_ftch.publication.validate import validate_card

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


def _render_job(job: Job, layout: CardLayout, profile: str = "channel") -> str:
    pub_card = build_card(job)
    outcome = validate_card(pub_card, layout)
    if not outcome.ok:
        return f"<b>{job.title or 'Job posting'}</b>"
    return render_card(pub_card, layout, profile=profile)


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
        layout: CardLayout | None = None,
        profile: str = "channel",
    ) -> None:
        self._client = client
        self._entity = entity
        self._own_client = own_client
        self._notify_mode = notify_mode
        self._notify_batch_size = notify_batch_size
        self._digest_formatter = digest_formatter
        self._pending_jobs: list[Job] = []
        self._layout = layout or load_layout()
        self._profile = profile

    async def emit(self, item: Job) -> None:
        if self._notify_mode == "instant":
            async with _client_session(self._client, own_client=self._own_client) as client:
                text = _render_job(item, self._layout, self._profile)
                await client.send_message(self._entity, text, link_preview=False)
        else:
            self._pending_jobs.append(item)

    async def flush(self) -> None:
        if not self._pending_jobs:
            return

        chunk_size = self._notify_batch_size
        async with _client_session(self._client, own_client=self._own_client) as client:
            for i in range(0, len(self._pending_jobs), chunk_size):
                chunk = self._pending_jobs[i : i + chunk_size]
                header = f"<b>Job Digest ({i + 1}-{i + len(chunk)})</b>\n\n"

                if self._digest_formatter is None:
                    digest = "\n\n".join(
                        f"<b>{j.title or '?'}</b> — {j.company or '?'}" for j in chunk
                    )
                else:
                    digest = self._digest_formatter(chunk, 0, chunk_size)

                await client.send_message(self._entity, header + digest, link_preview=False)
        self._pending_jobs.clear()
