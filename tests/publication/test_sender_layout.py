"""The bot senders always render the YAML card layout."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from job_ftch.adapters.telegram_bot.sender import ReplyCardSender, TelegramCardSender
from job_ftch.domain.models import JobRecord, LanguageCode, SourceKind, WorkMode


def _job() -> JobRecord:
    return JobRecord(
        raw_item_id="fmt-1",
        source_kind=SourceKind.CAREER_SITE,
        source_name="HH.ru",
        title="Senior ML Engineer",
        company="Яндекс",
        description="Building recommendation systems",
        canonical_url="https://hh.ru/vacancy/123",
        work_mode=WorkMode.HYBRID,
        city="Москва",
        country="Россия",
        language=LanguageCode.RU,
        requirements_must=("PyTorch", "Python"),
        tools_stack=("Python", "PyTorch"),
    )


def _incomplete_job() -> JobRecord:
    return JobRecord(
        raw_item_id="fmt-incomplete",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="AI Engineers Jobs",
        title="Специалист по нейросетям",
        description="Молодая команда автоматизирует процессы с помощью нейросетей.",
        canonical_url="https://t.me/example/1",
        language=LanguageCode.RU,
    )


def test_telegram_card_sender_defaults_to_yaml_layout() -> None:
    text = TelegramCardSender(bot=object())._render(_job())

    # New labelled-row layout.
    assert "Компания: Яндекс" in text
    assert "<b>Senior ML Engineer</b>" in text
    assert "Открыть вакансию" not in text


def test_reply_card_sender_defaults_to_yaml_layout() -> None:
    text = ReplyCardSender(message=object())._render(_job())

    assert "Компания: Яндекс" in text
    assert "Открыть вакансию" not in text


@pytest.mark.asyncio
async def test_channel_sends_rich_card_with_hashtags_link_keyboard_and_receipt() -> None:
    bot = AsyncMock()
    bot.send_rich_message.return_value = SimpleNamespace(
        chat=SimpleNamespace(id=-100), message_id=83
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✓", callback_data="ok")]]
    )
    receipt = await TelegramCardSender(bot, markup_for=lambda _: keyboard).send("@jobs", _job())
    call = bot.send_rich_message.await_args
    target, content = call.args
    assert target == "@jobs" and call.kwargs["reply_markup"] is keyboard
    assert "<p><b>Senior ML Engineer</b></p>" in content.html
    assert "#москва" in content.html and "#hybrid" in content.html
    assert 'href="https://hh.ru/vacancy/123"' in content.html
    assert not content.skip_entity_detection
    assert receipt.message_id == 83 and receipt.chat_id == "-100"
    bot.send_message.assert_not_called()


def test_incomplete_public_job_is_rejected_before_send() -> None:
    text = TelegramCardSender(bot=object())._render(_incomplete_job())

    assert text is None


def test_incomplete_control_bot_job_is_marked_for_review() -> None:
    text = ReplyCardSender(message=object())._render(_incomplete_job())

    assert text.startswith("⚠️ <i>Требует проверки</i>")
    assert "<b>Специалист по нейросетям</b>" in text
