"""The source keyboard must not grow with the number of sources.

One toggle button per source stacked a 17-source tenant into a 20-row keyboard that
buried the listing text it was supposed to act on.
"""

from typing import Any

import pytest

from job_ftch.adapters.telegram_bot.handlers.sources import (
    _SOURCES_PAGE_SIZE,
    build_sources_view,
    page_count,
)


def _sources(count: int, *, enabled: bool = True) -> list[dict[str, Any]]:
    return [
        {
            "source_id": f"career_site:site_{index}",
            "source_kind": "career_site",
            "source_name": f"site_{index}",
            "locator": f"https://example.com/{index}",
            "status": "healthy",
            "enabled": enabled,
        }
        for index in range(count)
    ]


def _toggle_buttons(markup: Any) -> list[Any]:
    return [
        button
        for row in markup.inline_keyboard
        for button in row
        if (button.callback_data or "").startswith("srci:")
    ]


def _page_buttons(markup: Any) -> list[Any]:
    return [
        button
        for row in markup.inline_keyboard
        for button in row
        if (button.callback_data or "").startswith("srcpg:")
    ]


@pytest.mark.parametrize(
    ("total", "expected"),
    [(0, 1), (1, 1), (8, 1), (9, 2), (17, 3)],
)
def test_page_count(total: int, expected: int) -> None:
    assert page_count(total) == expected


def test_seventeen_sources_do_not_produce_seventeen_toggles() -> None:
    _, markup = build_sources_view(_sources(17))

    toggles = _toggle_buttons(markup)
    assert len(toggles) == _SOURCES_PAGE_SIZE
    assert len(_page_buttons(markup)) == 2


def test_a_single_page_has_no_navigation() -> None:
    _, markup = build_sources_view(_sources(5))

    assert len(_toggle_buttons(markup)) == 5
    assert _page_buttons(markup) == []


def test_text_lists_only_the_current_page() -> None:
    text, _ = build_sources_view(_sources(17))

    assert "Всего: 17" in text
    assert "Показаны #1–#8" in text
    for index in range(8):
        assert f"https://example.com/{index}" in text
    assert "https://example.com/8" not in text

    second, _ = build_sources_view(_sources(17), page=1)
    assert "Показаны #9–#16" in second
    assert "https://example.com/8" in second
    assert "https://example.com/16" not in second


def test_large_source_inventory_stays_under_telegram_message_limit() -> None:
    text, _ = build_sources_view(_sources(63))

    assert len(text) < 4096


def test_long_source_diagnostics_are_compacted_under_telegram_message_limit() -> None:
    sources = _sources(63)
    for source in sources:
        source["status"] = "degraded"
        source["requirements"] = {"browser_setup_hint": " ".join(["install browser fallback"] * 80)}
        source["last_error"] = " ".join(["network timeout"] * 50)

    text, _ = build_sources_view(sources)

    assert len(text) < 4096
    assert text.count("install browser fallback") < 100


def test_toggle_indices_match_the_listing_on_later_pages() -> None:
    _, markup = build_sources_view(_sources(17), page=1)

    labels = [button.text for button in _toggle_buttons(markup)]
    assert labels[0].endswith("#9")
    assert labels[-1].endswith("#16")


def test_last_page_holds_the_remainder() -> None:
    _, markup = build_sources_view(_sources(17), page=2)

    toggles = _toggle_buttons(markup)
    assert len(toggles) == 1
    assert toggles[0].text.endswith("#17")


def test_out_of_range_page_is_clamped() -> None:
    """A stale keyboard can point past the end after sources are removed."""
    _, markup = build_sources_view(_sources(17), page=99)

    assert _toggle_buttons(markup)[0].text.endswith("#17")

    _, negative = build_sources_view(_sources(17), page=-3)
    assert _toggle_buttons(negative)[0].text.endswith("#1")


def test_navigation_wraps_around() -> None:
    _, first = build_sources_view(_sources(17), page=0)
    previous, following = _page_buttons(first)

    assert previous.callback_data == "srcpg:2"
    assert following.callback_data == "srcpg:1"


def test_disabled_sources_offer_enable() -> None:
    _, markup = build_sources_view(_sources(3, enabled=False))

    assert all(button.callback_data.startswith("srci:enable") for button in _toggle_buttons(markup))


def test_bulk_actions_are_always_present() -> None:
    _, markup = build_sources_view(_sources(17))

    actions = {
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if (button.callback_data or "").startswith("src:")
    }
    assert {"src:add", "src:run", "src:clear"} <= actions
