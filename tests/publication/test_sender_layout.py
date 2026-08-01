"""The bot senders must render the YAML card layout by default.

Regression guard for the release gap where the scheduler and ``/run`` built
``TelegramCardSender`` / ``ReplyCardSender`` without a ``layout``, so
``_render`` silently fell back to the legacy ``format_vacancy_card`` and the
v0.0.6 card renderer never reached the channel.
"""

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


def test_telegram_card_sender_defaults_to_yaml_layout() -> None:
    text = TelegramCardSender(bot=object())._render(_job())

    # New labelled-row layout, not the legacy "🔗 Открыть вакансию" card.
    assert "Компания: Яндекс" in text
    assert "<b>Senior ML Engineer</b>" in text
    assert "Открыть вакансию" not in text


def test_reply_card_sender_defaults_to_yaml_layout() -> None:
    text = ReplyCardSender(message=object())._render(_job())

    assert "Компания: Яндекс" in text
    assert "Открыть вакансию" not in text
