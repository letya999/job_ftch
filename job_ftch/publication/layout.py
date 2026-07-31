"""YAML-driven publication layout engine.

Loads a declarative card layout from ``config/publication/card.yaml``
and resolves field values against a ``PublicationCard``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class BlockSpec:
    field: str | None = None
    spacer: bool = False
    style: str | None = None
    max_len: int | None = None
    prefix: str | None = None
    join: str | None = None
    order: tuple[str, ...] = ()
    max_items: int | None = None
    omit_if_empty: bool = False
    # Rendered in place of an empty value. A labelled row that says the data is
    # missing tells the reader more than a silently absent line.
    placeholder: str | None = None


@dataclass(frozen=True)
class FooterSpec:
    template: str = "{auto_mark}"
    auto_mark: str = ""
    link_labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FormattingSpec:
    leading_emoji: bool = False
    italic: bool = False
    currency_symbols: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SinkProfile:
    capabilities: tuple[str, ...] = ()
    feedback: bool = False


@dataclass(frozen=True)
class CardLayout:
    version: int = 1
    blocks: tuple[BlockSpec, ...] = ()
    footer: FooterSpec = field(default_factory=FooterSpec)
    formatting: FormattingSpec = field(default_factory=FormattingSpec)
    banlist: tuple[str, ...] = ()
    profiles: dict[str, SinkProfile] = field(default_factory=dict)


def _parse_block(raw: dict[str, Any]) -> BlockSpec:
    order = raw.get("order", ())
    if isinstance(order, list):
        order = tuple(order)
    return BlockSpec(
        field=raw.get("field"),
        spacer=raw.get("spacer", False),
        style=raw.get("style"),
        max_len=raw.get("max_len"),
        prefix=raw.get("prefix"),
        join=raw.get("join"),
        order=order,
        max_items=raw.get("max_items"),
        omit_if_empty=raw.get("omit_if_empty", False),
        placeholder=raw.get("placeholder"),
    )


def _parse_footer(raw: dict[str, Any] | None) -> FooterSpec:
    if not raw:
        return FooterSpec()
    return FooterSpec(
        template=raw.get("template", "{auto_mark}"),
        auto_mark=raw.get("auto_mark", ""),
        link_labels=raw.get("link_labels", {}),
    )


def _parse_formatting(raw: dict[str, Any] | None) -> FormattingSpec:
    if not raw:
        return FormattingSpec()
    return FormattingSpec(
        leading_emoji=raw.get("leading_emoji", False),
        italic=raw.get("italic", False),
        currency_symbols=raw.get("currency_symbols", {}),
    )


def _parse_profile(raw: dict[str, Any]) -> SinkProfile:
    caps = raw.get("capabilities", ())
    if isinstance(caps, list):
        caps = tuple(caps)
    return SinkProfile(capabilities=caps, feedback=raw.get("feedback", False))


def load_layout(path: str | Path | None = None) -> CardLayout:
    """Load a CardLayout from a YAML file.

    Falls back to sensible built-in defaults when no file is provided
    or the file is missing.
    """
    if path is not None:
        p = Path(path)
        if p.exists():
            with p.open(encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            return _parse_layout(data)

    return _default_layout()


def _parse_layout(data: dict[str, Any]) -> CardLayout:
    blocks = tuple(_parse_block(b) for b in data.get("blocks", []))
    banlist_raw = data.get("banlist", [])
    banlist = tuple(banlist_raw) if isinstance(banlist_raw, list) else ()
    profiles_raw = data.get("profiles", {})
    profiles = {name: _parse_profile(prof) for name, prof in profiles_raw.items()}
    return CardLayout(
        version=data.get("version", 1),
        blocks=blocks,
        footer=_parse_footer(data.get("footer")),
        formatting=_parse_formatting(data.get("formatting")),
        banlist=banlist,
        profiles=profiles,
    )


def _default_layout() -> CardLayout:
    return CardLayout(
        version=1,
        blocks=(
            BlockSpec(field="role", style="bold", max_len=90),
            BlockSpec(spacer=True),
            BlockSpec(field="company", prefix="Компания: ", placeholder="не указана"),
            BlockSpec(field="geo", prefix="Гео: ", max_len=90, placeholder="не указано"),
            BlockSpec(field="work_format", prefix="Формат: ", placeholder="не указан"),
            BlockSpec(field="salary", prefix="Условия: ", placeholder="не указаны"),
            BlockSpec(spacer=True),
            BlockSpec(field="summary", max_len=180, omit_if_empty=True),
            BlockSpec(field="key_requirements", prefix="Нужно: ", max_len=160, omit_if_empty=True),
            BlockSpec(field="stack", prefix="Стек: ", max_len=200, omit_if_empty=True),
        ),
        footer=FooterSpec(
            template="{source_label} {link}",
            auto_mark='🤖 <a href="https://github.com/letya999/job">job_ftch</a>',
            link_labels={
                "career_site": "открыть вакансию",
                "telegram_channel": "открыть пост",
                "telegram_group": "открыть пост",
                "default": "открыть",
            },
        ),
        formatting=FormattingSpec(leading_emoji=False, italic=False),
        banlist=(
            "Войти и откликнуться",
            "Show contacts",
            "Report",
            "Навыки:",
            "Квалификация:",
            "Специализации:",
            "Откликнуться",
        ),
        profiles={
            "channel": SinkProfile(capabilities=("html", "link_in_footer"), feedback=False),
            "control_bot": SinkProfile(capabilities=("html", "inline_keyboard"), feedback=True),
        },
    )
