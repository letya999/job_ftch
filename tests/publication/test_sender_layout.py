"""The bot senders always render the YAML card layout."""

from __future__ import annotations

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


def test_incomplete_public_job_is_rejected_before_send() -> None:
    text = TelegramCardSender(bot=object())._render(_incomplete_job())

    assert text is None


def test_incomplete_control_bot_job_is_marked_for_review() -> None:
    text = ReplyCardSender(message=object())._render(_incomplete_job())

    assert text.startswith("⚠️ <i>Требует проверки</i>")
    assert "<b>Специалист по нейросетям</b>" in text
