"""aiogram transport for the application-level publisher.

The only Telegram-specific parts of delivery: rendering a card and translating
aiogram exceptions into the transport-neutral errors the publisher understands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from job_ftch.adapters.telegram_bot.formatter import format_vacancy_card
from job_ftch.application.channel_publisher import FatalTargetError, TransientSendError
from job_ftch.publication.card import build_card
from job_ftch.publication.layout import CardLayout, load_layout
from job_ftch.publication.render import render_card
from job_ftch.publication.validate import validate_card

if TYPE_CHECKING:
    from collections.abc import Callable

    from aiogram import Bot
    from aiogram.types import InlineKeyboardMarkup, Message

    from job_ftch.domain import Job

logger = structlog.get_logger(__name__)


def _translate(error: Exception) -> Exception:
    """Map aiogram failures onto the transport-neutral publisher contract."""
    if isinstance(error, TelegramRetryAfter):
        return TransientSendError(error.retry_after, str(error))
    if isinstance(error, TelegramForbiddenError):
        return FatalTargetError(str(error))
    return error


def _render_with_layout(job: Job, layout: CardLayout, profile: str) -> str:
    """Render the YAML card, degrading to the legacy card on any failure.

    A card that cannot be built or fails validation must not drop the job: fall
    back to ``format_vacancy_card`` and log, so the failure is visible instead of
    silently swallowed the way the missing-layout default used to swallow it.
    """
    try:
        card = build_card(job)
        if not validate_card(card, layout).ok:
            logger.warning("card_validation_failed", job=str(getattr(job, "title", "")))
            return format_vacancy_card(job)
        return render_card(card, layout, profile=profile)
    except Exception as exc:  # noqa: BLE001 - never lose a job over a render error
        logger.warning("card_render_failed", error=str(exc))
        return format_vacancy_card(job)


class TelegramCardSender:
    """Publishes a card to an arbitrary chat or channel id.

    ``markup_for`` lets the caller attach per-job buttons - the reader feedback control -
    without this transport knowing what they mean. Returning None keeps the plain card.

    The YAML-driven renderer (``config/publication/card.yaml``) is loaded by
    default, matching ``TelegramPostingSink``; pass an explicit ``layout`` to
    override it. ``_render`` still degrades to the legacy card only if a layout
    cannot be resolved or a card fails validation.
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
        self._layout = layout or load_layout()
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
        return _render_with_layout(job, self._layout, self._profile)


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
        self._layout = layout or load_layout()
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
        return _render_with_layout(job, self._layout, self._profile)
