"""Tests for pre-send card validation."""

from __future__ import annotations

from job_ftch.publication.card import PublicationCard
from job_ftch.publication.layout import load_layout
from job_ftch.publication.validate import validate_card


def _card(**overrides: object) -> PublicationCard:
    defaults: dict[str, object] = {
        "role": "ML Engineer",
        "company": "Acme Corp",
        "url": "https://example.com/job/1",
        "source_name": "TestSource",
    }
    defaults.update(overrides)
    return PublicationCard(**defaults)  # type: ignore[arg-type]


class TestValidateCard:
    def test_valid_card(self) -> None:
        layout = load_layout()
        outcome = validate_card(_card(), layout)
        assert outcome.ok is True
        assert outcome.reject_reason is None

    def test_missing_role_rejects(self) -> None:
        layout = load_layout()
        card = PublicationCard(role=" ")
        outcome = validate_card(card, layout)
        assert outcome.ok is False
        assert outcome.reject_reason == "missing_role"

    def test_banlist_in_summary_warns(self) -> None:
        layout = load_layout()
        outcome = validate_card(
            _card(summary="Great job. Войти и откликнуться. Apply now."),
            layout,
        )
        assert outcome.ok is True
        assert any("banlist" in w for w in outcome.warnings)

    def test_title_echo_warns(self) -> None:
        layout = load_layout()
        outcome = validate_card(
            _card(role="ML Engineer", summary="ML Engineer at a great company"),
            layout,
        )
        assert any("title_echo" in w for w in outcome.warnings)

    def test_salary_no_digits_warns(self) -> None:
        layout = load_layout()
        outcome = validate_card(_card(salary="competitive"), layout)
        assert any("salary_no_digits" in w for w in outcome.warnings)

    def test_unsafe_url_warns(self) -> None:
        layout = load_layout()
        outcome = validate_card(_card(url="ftp://evil.com/payload"), layout)
        assert any("unsafe_url" in w for w in outcome.warnings)

    def test_clean_card_no_warnings(self) -> None:
        layout = load_layout()
        outcome = validate_card(
            _card(
                salary="200 000–400 000 ₽/мес",
                summary="Building ML pipelines.",
            ),
            layout,
        )
        assert len(outcome.warnings) == 0


class TestVacancySubstanceGate:
    """A chat message about jobs has a topic but no employer, place, money or
    requirements. Upstream routing accepted such posts; this is the last gate
    before a public channel."""

    def test_chat_message_rejected(self) -> None:
        layout = load_layout()
        card = _card(
            role="LLM-инженер, я так понимаю, это не вайбкодер, верно?",
            company=None,
            stack="machine learning · large language models",
        )
        outcome = validate_card(card, layout)
        assert outcome.ok is False
        assert outcome.reject_reason == "no_vacancy_substance"

    def test_stack_alone_is_not_substance(self) -> None:
        layout = load_layout()
        outcome = validate_card(_card(company=None, stack="python · pytorch"), layout)
        assert outcome.ok is False

    def test_requirements_alone_is_substance(self) -> None:
        """Postings that name no employer still qualify if they state requirements."""
        layout = load_layout()
        outcome = validate_card(_card(company=None, key_requirements="3+ years Python"), layout)
        assert outcome.ok is True

    def test_geo_alone_is_substance(self) -> None:
        layout = load_layout()
        assert validate_card(_card(company=None, geo="Москва"), layout).ok is True

    def test_salary_alone_is_substance(self) -> None:
        layout = load_layout()
        assert validate_card(_card(company=None, salary="200 000 ₽/мес"), layout).ok is True
