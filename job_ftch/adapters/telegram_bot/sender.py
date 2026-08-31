"""aiogram transport for the application-level publisher.

The only Telegram-specific parts of delivery: rendering a card and translating
aiogram exceptions into the transport-neutral errors the publisher understands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

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


def _render_with_layout(
    job: Job,
    layout: CardLayout,
    profile: str,
    reject_invalid: bool = False,
) -> str | None:
    """Render every publication through the YAML card layout.

    Public channel cards that fail the substance gate are skipped. Control-bot
    replies may still show the marked card to the operator for review.
    Technical rendering errors are raised to the publisher and are never
    replaced with a differently formatted message.
    """
    card = build_card(job)
    outcome = validate_card(card, layout)
    if not outcome.ok:
        logger.info(
            "publication_card_rejected" if reject_invalid else "card_validation_failed_marked",
            job=str(getattr(job, "title", "")),
            reason=outcome.reject_reason,
        )
        if reject_invalid:
            return None
        return f"⚠️ <i>Требует проверки</i>\n\n{render_card(card, layout, profile=profile)}"
    return render_card(card, layout, profile=profile)


class TelegramCardSender:
    """Publishes a card to an arbitrary chat or channel id.

    ``markup_for`` lets the caller attach per-job buttons - the reader feedback control -
    without this transport knowing what they mean. Returning None keeps the plain card.

    The YAML-driven renderer (``config/publication/card.yaml``) is loaded by
    default, matching ``TelegramPostingSink``; pass an explicit ``layout`` to
    override it. Validation failures are rejected for public channel delivery;
    control-bot replies retain the review marker.
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
            if text is None:
                return
            await self._bot.send_message(
                target,
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=self._markup_for(job) if self._markup_for else None,
            )
        except Exception as error:
            raise _translate(error) from error

    def _render(self, job: Job) -> str | None:
        return _render_with_layout(job, self._layout, self._profile, True)


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
        rendered = _render_with_layout(job, self._layout, self._profile)
        assert rendered is not None
        return rendered
