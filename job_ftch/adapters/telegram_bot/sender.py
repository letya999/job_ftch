"""aiogram transport for the application-level publisher.

The only Telegram-specific parts of delivery: rendering a card and translating
aiogram exceptions into the transport-neutral errors the publisher understands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from job_ftch.adapters.telegram_bot.formatter import format_vacancy_card
from job_ftch.application.channel_publisher import FatalTargetError, TransientSendError
from job_ftch.publication.card import build_card
from job_ftch.publication.layout import CardLayout
from job_ftch.publication.render import render_card
from job_ftch.publication.validate import validate_card

if TYPE_CHECKING:
    from collections.abc import Callable

    from aiogram import Bot
    from aiogram.types import InlineKeyboardMarkup, Message

    from job_ftch.domain import Job


def _translate(error: Exception) -> Exception:
    """Map aiogram failures onto the transport-neutral publisher contract."""
    if isinstance(error, TelegramRetryAfter):
        return TransientSendError(error.retry_after, str(error))
    if isinstance(error, TelegramForbiddenError):
        return FatalTargetError(str(error))
    return error


class TelegramCardSender:
    """Publishes a card to an arbitrary chat or channel id.

    ``markup_for`` lets the caller attach per-job buttons - the reader feedback control -
    without this transport knowing what they mean. Returning None keeps the plain card.

    When ``layout`` is provided the new YAML-driven renderer is used; otherwise
    falls back to the legacy ``format_vacancy_card``.
    """

    def __init__(
        self,
        bot: Bot,
        *,
        markup_for: Callable[[Job], InlineKeyboardMarkup | None] | None = None,
        layout: CardLayout | None = None,
        profile: str = "channel",
    ) -> None:
        self._bot = bot
        self._markup_for = markup_for
        self._layout = layout
        self._profile = profile

    async def send(self, target: str, job: Job) -> None:
        try:
            text = self._render(job)
            await self._bot.send_message(
                target,
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=self._markup_for(job) if self._markup_for else None,
            )
        except Exception as error:
            raise _translate(error) from error

    def _render(self, job: Job) -> str:
        if self._layout is None:
            return format_vacancy_card(job)
        pub_card = build_card(job)
        outcome = validate_card(pub_card, self._layout)
        if not outcome.ok:
            return format_vacancy_card(job)
        return render_card(pub_card, self._layout, profile=self._profile)


class ReplyCardSender:
    """Answers into the chat that issued the command.

    Kept distinct from `TelegramCardSender` because replying is the established
    transport for ``/run`` results; only the retry semantics are shared.
    """

    def __init__(
        self,
        message: Message,
        *,
        layout: CardLayout | None = None,
        profile: str = "control_bot",
    ) -> None:
        self._message = message
        self._layout = layout
        self._profile = profile

    async def send(self, _target: str, job: Job) -> None:
        try:
            text = self._render(job)
            await self._message.answer(
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as error:
            raise _translate(error) from error

    def _render(self, job: Job) -> str:
        if self._layout is None:
            return format_vacancy_card(job)
        pub_card = build_card(job)
        outcome = validate_card(pub_card, self._layout)
        if not outcome.ok:
            return format_vacancy_card(job)
        return render_card(pub_card, self._layout, profile=self._profile)
