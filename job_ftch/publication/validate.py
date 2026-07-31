"""Pre-send validation for publication cards.

Rules are data-driven from the layout banlist and structural checks.
A failing field is dropped rather than rejecting the whole card, unless
the card falls below minimum (no role or no url).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_ftch.publication.card import PublicationCard
    from job_ftch.publication.layout import CardLayout


@dataclass
class ValidationOutcome:
    ok: bool = True
    warnings: list[str] = field(default_factory=list)
    reject_reason: str | None = None


# A posting states at least one of these. A chat message about jobs does not:
# it has a topic (so a stack can be inferred) but no employer, no place, no
# money and no requirements. Upstream routing accepted such messages with
# review_reasons already set to missing_company/missing_location, so this is
# the last gate before they reach a public channel.
_SUBSTANCE_FIELDS = ("company", "geo", "salary", "key_requirements")


def validate_card(card: PublicationCard, layout: CardLayout) -> ValidationOutcome:
    outcome = ValidationOutcome()

    if not card.role or not card.role.strip():
        outcome.ok = False
        outcome.reject_reason = "missing_role"
        return outcome

    if not any((getattr(card, name, None) or "").strip() for name in _SUBSTANCE_FIELDS):
        outcome.ok = False
        outcome.reject_reason = "no_vacancy_substance"
        return outcome

    for phrase in layout.banlist:
        lp = phrase.lower()
        if card.summary and lp in card.summary.lower():
            outcome.warnings.append(f"banlist_in_summary:{phrase}")
        if card.key_requirements and lp in card.key_requirements.lower():
            outcome.warnings.append(f"banlist_in_requirements:{phrase}")

    if (
        card.summary
        and card.role
        and card.summary.strip().lower().startswith(card.role.strip().lower())
    ):
        outcome.warnings.append("title_echo_in_summary")

    if card.salary and not any(c.isdigit() for c in card.salary):
        outcome.warnings.append("salary_no_digits")

    if card.url:
        safe_schemes = ("https://", "http://", "t.me/", "telegram.me/", "tg://")
        if not any(card.url.startswith(s) for s in safe_schemes):
            outcome.warnings.append("unsafe_url_scheme")

    return outcome
