"""Tests for the capability-aware card renderer."""

from __future__ import annotations

from job_ftch.publication.card import PublicationCard
from job_ftch.publication.layout import load_layout
from job_ftch.publication.render import SinkCapabilities, render_card


def _card(**overrides: object) -> PublicationCard:
    defaults: dict[str, object] = {
        "role": "Senior ML Engineer",
        "company": "Acme Corp",
        "geo": "Berlin",
        "work_format": "удалённо",
        "salary": "200 000–400 000 ₽/мес",
        "summary": "Building production ML pipelines for recommendation systems.",
        "key_requirements": "3+ years ML, PyTorch, English B2",
        "stack": "Python · PyTorch · Docker · K8s",
        "url": "https://example.com/job/42",
        "source_name": "HH.ru",
        "source_kind": "career_site",
        "language": "en",
        "job_id": "abc123",
    }
    defaults.update(overrides)
    return PublicationCard(**defaults)  # type: ignore[arg-type]


class TestRenderCard:
    def test_default_layout_channel(self) -> None:
        layout = load_layout()
        text = render_card(_card(), layout, profile="channel")
        assert "<b>Senior ML Engineer</b>" in text
        assert "Acme Corp" in text
        assert "200 000" in text
        assert "Berlin" in text
        assert "открыть вакансию" in text
        assert "HH.ru" in text

    def test_no_leading_emoji(self) -> None:
        layout = load_layout()
        text = render_card(_card(), layout, profile="channel")
        assert not text.startswith("🔵")

    def test_no_italic(self) -> None:
        layout = load_layout()
        text = render_card(_card(), layout, profile="channel")
        assert "<i>" not in text

    def test_summary_rendered(self) -> None:
        layout = load_layout()
        text = render_card(_card(), layout, profile="channel")
        assert "Building production ML" in text

    def test_stack_rendered(self) -> None:
        layout = load_layout()
        text = render_card(_card(), layout, profile="channel")
        assert "Стек:" in text
        assert "Python" in text

    def test_requirements_rendered(self) -> None:
        layout = load_layout()
        text = render_card(_card(), layout, profile="channel")
        assert "Нужно:" in text
        assert "3+ years ML" in text

    def test_empty_optional_fields_omitted(self) -> None:
        layout = load_layout()
        text = render_card(_card(summary=None, key_requirements=None, stack=None), layout)
        assert "Стек:" not in text
        assert "Нужно:" not in text
        assert "<b>Senior ML Engineer</b>" in text

    def test_footer_link_telegram(self) -> None:
        layout = load_layout()
        text = render_card(
            _card(source_kind="telegram_channel", url="https://t.me/channel/123"),
            layout,
        )
        assert "открыть пост" in text

    def test_banlist_stripped(self) -> None:
        layout = load_layout()
        text = render_card(
            _card(summary="Apply now. Войти и откликнуться. Great job."),
            layout,
        )
        assert "Войти и откликнуться" not in text

    def test_control_bot_profile(self) -> None:
        layout = load_layout()
        text = render_card(_card(), layout, profile="control_bot")
        assert "<b>Senior ML Engineer</b>" in text

    def test_truncation_respects_max_len(self) -> None:
        layout = load_layout()
        long_card = _card(
            role="A" * 200,
            summary="B" * 300,
            key_requirements="C" * 300,
            stack="D" * 200,
        )
        text = render_card(long_card, layout, profile="channel")
        assert len(text) <= 3900

    def test_spacer_produces_empty_line(self) -> None:
        layout = load_layout()
        text = render_card(_card(), layout, profile="channel")
        assert "\n\n" in text

    def test_labels_do_not_follow_card_language(self) -> None:
        """Chrome is uniform; only the posting's own text carries its language."""
        layout = load_layout()
        ru = render_card(_card(language="ru"), layout, profile="channel")
        en = render_card(_card(language="en"), layout, profile="channel")
        for label in ("Компания:", "Гео:", "Формат:", "Условия:", "Нужно:", "Стек:"):
            assert label in ru and label in en
        assert "открыть вакансию" in ru and "открыть вакансию" in en

    def test_auto_mark_in_footer(self) -> None:
        layout = load_layout()
        text = render_card(_card(), layout, profile="channel")
        assert "job_ftch" in text
        assert "github.com/letya999/job" in text

    def test_footer_survives_truncation(self) -> None:
        """An oversized body must not eat into the footer's anchor markup."""
        layout = load_layout()
        text = render_card(_card(role="A" * 200, summary="B" * 300), layout)
        assert text.count("<a ") == text.count("</a>")
        assert text.endswith("</a>")
        assert "github.com/letya999/job" in text
        assert "https://example.com/job/42" in text


class TestSinkCapabilities:
    def test_for_profile_channel(self) -> None:
        layout = load_layout()
        caps = SinkCapabilities.for_profile("channel", layout)
        assert caps.html is True
        assert caps.link_in_footer is True
        assert caps.inline_keyboard is False

    def test_for_profile_control_bot(self) -> None:
        layout = load_layout()
        caps = SinkCapabilities.for_profile("control_bot", layout)
        assert caps.html is True
        assert caps.inline_keyboard is True

    def test_for_unknown_profile(self) -> None:
        layout = load_layout()
        caps = SinkCapabilities.for_profile("nonexistent", layout)
        assert caps.html is True
