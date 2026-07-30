"""Capability-aware card renderer.

Takes a ``PublicationCard`` + ``CardLayout`` and produces a plain string
for a specific sink, respecting the sink's declared capabilities.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_ftch.publication.card import PublicationCard
    from job_ftch.publication.layout import BlockSpec, CardLayout


@dataclass(frozen=True)
class SinkCapabilities:
    html: bool = True
    inline_keyboard: bool = False
    link_in_footer: bool = True
    max_len: int = 3800

    @classmethod
    def for_profile(cls, profile_name: str, layout: CardLayout) -> SinkCapabilities:
        prof = layout.profiles.get(profile_name)
        if prof is None:
            return cls()
        caps = set(prof.capabilities)
        return cls(
            html="html" in caps,
            inline_keyboard="inline_keyboard" in caps,
            link_in_footer="link_in_footer" in caps,
        )


def render_card(
    card: PublicationCard,
    layout: CardLayout,
    *,
    profile: str = "channel",
) -> str:
    """Render a card to an HTML string for the given sink profile."""
    caps = SinkCapabilities.for_profile(profile, layout)
    lines: list[str] = []

    for block in layout.blocks:
        line = _render_block(block, card, layout, caps)
        if line is None:
            continue
        lines.append(line)

    footer = _render_footer(card, layout)
    if footer:
        lines.append("")
        lines.append(footer)

    text = "\n".join(lines)
    text = _strip_banlist(text, layout.banlist)
    return _safe_truncate(text, caps.max_len)


def _resolve_field(field_name: str, card: PublicationCard) -> str | None:
    val = getattr(card, field_name, None)
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _render_block(
    block: BlockSpec,
    card: PublicationCard,
    layout: CardLayout,
    caps: SinkCapabilities,
) -> str | None:
    if block.spacer:
        return ""

    if block.field == "conditions":
        return _render_conditions(block, card, caps)

    raw = _resolve_field(block.field or "", card)
    if raw is None:
        if block.omit_if_empty:
            return None
        return None

    text = escape(raw) if caps.html else raw
    if block.max_len:
        text = _truncate_word(text, block.max_len)

    if block.prefix:
        text = f"{escape(block.prefix) if caps.html else block.prefix}{text}"

    if block.style == "bold" and caps.html:
        text = f"<b>{text}</b>"

    return text


def _render_conditions(
    block: BlockSpec,
    card: PublicationCard,
    caps: SinkCapabilities,
) -> str | None:
    parts: list[str] = []
    for sub_field in block.order:
        val = _resolve_field(sub_field, card)
        if val:
            parts.append(escape(val) if caps.html else val)
    if not parts:
        return None
    joiner = block.join or " · "
    line = joiner.join(parts)
    if block.max_len:
        line = _truncate_word(line, block.max_len)
    return line


def _render_footer(card: PublicationCard, layout: CardLayout) -> str:
    footer = layout.footer
    url = card.url

    source_label = card.source_name or ""
    link_label = footer.link_labels.get(
        card.source_kind or "default",
        footer.link_labels.get("default", "открыть"),
    )

    if url:
        safe_url = _escape_url(url)
        link = f'<a href="{safe_url}">{escape(link_label)}</a>'
    else:
        link = ""

    auto_mark = footer.auto_mark or ""

    parts: list[str] = []
    if source_label:
        parts.append(escape(source_label))
    if link:
        parts.append(link)
    if auto_mark:
        parts.append(escape(auto_mark))

    return " · ".join(parts)


def _strip_banlist(text: str, banlist: tuple[str, ...]) -> str:
    for phrase in banlist:
        text = text.replace(escape(phrase), "")
        text = text.replace(phrase, "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _truncate_word(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rsplit(" ", 1)[0]
    if not cut:
        cut = text[:max_len]
    return cut.rstrip(".,;: ") + "..."


def _safe_truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    truncated = re.sub(r"&[^;]{0,10}$", "", truncated)
    truncated = re.sub(r"<[^>]{0,100}$", "", truncated)
    return truncated.rstrip() + "..."


def _escape_url(url: str) -> str:
    return url.replace("&", "&amp;").replace('"', "%22").replace("<", "%3C").replace(">", "%3E")
