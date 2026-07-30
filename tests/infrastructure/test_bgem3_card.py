from __future__ import annotations

from job_ftch.domain.bgem3_card import build_bgem3_card


def test_bgem3_card_prefers_structured_vacancy_fields() -> None:
    card = build_bgem3_card(
        "boilerplate announcement",
        metadata={
            "title": "Senior ML Engineer",
            "responsibilities": ["Build RAG services", "Own evaluation"],
            "requirements": ["Python", "LLM"],
        },
    )

    assert "vacancy title: Senior ML Engineer" in card
    assert "responsibilities: Build RAG services; Own evaluation" in card
    assert "requirements: Python; LLM" in card
    assert "description: boilerplate announcement" in card


def test_bgem3_card_is_stable_under_whitespace_changes() -> None:
    assert build_bgem3_card("Senior ML Engineer\n\nRemote") == build_bgem3_card(
        "  Senior ML Engineer   \n Remote  "
    )


def test_bgem3_card_uses_first_source_line_as_unstructured_title() -> None:
    card = build_bgem3_card("Senior ML Engineer\nRemote role building retrieval")

    assert "vacancy title: Senior ML Engineer" in card
    assert "vacancy title: Senior ML Engineer Remote role" not in card
