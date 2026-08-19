"""Helpers for resolving simple user input into concrete SourceSpec payloads."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import structlog

from job_ftch.domain.runtime_source import source_spec_name
from job_ftch.domain.source_spec import (
    CareerSiteSpec,
    RSSFeedSourceSpec,
    SourceSpec,
    TelegramChannelSpec,
    TelegramGroupSpec,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from contextlib import AbstractAsyncContextManager

    from job_ftch.application.contracts import AuthProvider

logger = structlog.get_logger(__name__)

_SOURCE_TYPE_HINTS = {
    "site": "career_site",
    "url": "career_site",
    "career": "career_site",
    "career_site": "career_site",
    "rss": "rss_feed",
    "rss_feed": "rss_feed",
    "telegram": "telegram",
    "tg": "telegram",
    "telegram_channel": "telegram_channel",
    "tg_channel": "telegram_channel",
    "telegram_group": "telegram_group",
    "tg_group": "telegram_group",
}


def _split_inline_source_type(value: str, source_type: str | None) -> tuple[str, str | None]:
    if source_type is not None:
        return value, source_type
    prefix, sep, rest = value.partition(":")
    if not sep:
        return value, source_type
    normalized = _SOURCE_TYPE_HINTS.get(prefix.strip().casefold())
    if normalized is None or not rest.strip():
        return value, source_type
    return rest.strip(), normalized


def _normalize_telegram_entity(value: str) -> str | None:
    stripped = value.strip()
    if not stripped:
        return None
    if stripped.startswith("t.me/"):
        stripped = f"https://{stripped}"
    if "://" in stripped and not (
        stripped.startswith("https://t.me/")
        or stripped.startswith("http://t.me/")
        or stripped.startswith("tg://")
    ):
        return None
    if stripped.startswith("@"):
        stripped = stripped.removeprefix("@")
    if stripped.startswith("tg://"):
        parsed = urlsplit(stripped)
        query = parse_qs(parsed.query)
        domain = query.get("domain", [""])[0].strip()
        return domain.removeprefix("@") or None
    if stripped.startswith("https://t.me/") or stripped.startswith("http://t.me/"):
        parsed = urlsplit(stripped)
        path = parsed.path.strip("/")
        if not path:
            return None
        return path.split("/", maxsplit=1)[0].removeprefix("@") or None
    return stripped.removeprefix("@") or None


def _is_telegram_input(value: str) -> bool:
    stripped = value.strip()
    return (
        stripped.startswith("@")
        or stripped.startswith("t.me/")
        or stripped.startswith("https://t.me/")
        or stripped.startswith("http://t.me/")
        or stripped.startswith("tg://resolve")
    )


async def _detect_telegram_source_type(
    entity: str,
    *,
    auth_provider: AuthProvider | None,
    auth_source_id: str | None = None,
    telegram_client_factory: Callable[[str | None, AuthProvider], AbstractAsyncContextManager[Any]]
    | None = None,
) -> str:
    if auth_provider is None or telegram_client_factory is None:
        return "telegram_channel"
    try:
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _run_factory() -> AsyncGenerator[Any, None]:
            async with telegram_client_factory(auth_source_id, auth_provider) as managed_client:
                yield managed_client

        async with _run_factory() as managed_client:
            chat = await managed_client.get_entity(entity)
    except Exception as exc:
        logger.warning(
            "telegram_source_probe_failed",
            entity=entity,
            error=str(exc),
        )
        return "telegram_channel"

    if bool(getattr(chat, "broadcast", False)) and not bool(getattr(chat, "megagroup", False)):
        return "telegram_channel"
    return "telegram_group"


def _recipe_source_name(value: str) -> str | None:
    """Map a recipe/public fixture locator to its canonical source_name."""
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "fixtures" / "sources" / "ai_jobs.json"
    if not path.is_file():
        return None
    try:
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    needle = value.strip().rstrip("/")
    for item in payload.get("sources") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("source_name")
        if not isinstance(name, str) or not name.strip():
            continue
        locators = [
            str(item.get("url") or "").strip().rstrip("/"),
            str(item.get("entity") or "").strip().lstrip("@"),
            str(item.get("feed_url") or "").strip().rstrip("/"),
        ]
        if needle in locators or needle.lstrip("@") in locators:
            return name.strip()
        if needle.endswith("/" + locators[1]) and locators[1]:
            return name.strip()
        if (
            needle.startswith("https://t.me/")
            and locators[1]
            and needle.rsplit("/", 1)[-1] == locators[1]
        ):
            return name.strip()
        if (
            needle.startswith("http://t.me/")
            and locators[1]
            and needle.rsplit("/", 1)[-1] == locators[1]
        ):
            return name.strip()
    return None


def _with_source_name(spec: SourceSpec, source_name: str | None, *locators: str) -> SourceSpec:
    explicit = (source_name or "").strip() or None
    chosen = explicit
    if chosen is None:
        for locator in locators:
            if locator:
                chosen = _recipe_source_name(locator)
                if chosen:
                    break
    if chosen is None:
        chosen = getattr(spec, "source_name", None) or source_spec_name(spec)
    copier = getattr(spec, "model_copy", None)
    if not callable(copier):
        return spec
    copied = copier(update={"source_name": chosen})
    return copied if isinstance(copied, type(spec)) else spec


async def build_source_spec_from_input(
    value: str,
    *,
    auth_provider: AuthProvider | None = None,
    source_type: str | None = None,
    limit: int = 100,
    auth_source_id: str | None = None,
    telegram_client_factory: Callable[[str | None, AuthProvider], AbstractAsyncContextManager[Any]]
    | None = None,
    runtime_defaults_fn: Callable[[CareerSiteSpec], CareerSiteSpec] | None = None,
    source_name: str | None = None,
) -> SourceSpec:
    stripped, source_type = _split_inline_source_type(value.strip(), source_type)
    if not stripped:
        msg = "Source input must not be blank."
        raise ValueError(msg)

    normalized_type = (source_type or "auto").strip().casefold()
    is_telegram = _is_telegram_input(stripped)
    if normalized_type in {"telegram", "telegram_channel", "telegram_group"} or is_telegram:
        entity = _normalize_telegram_entity(stripped)
        if entity is None:
            msg = f"Unsupported Telegram source input: {value}"
            raise ValueError(msg)
        resolved_type = normalized_type
        if resolved_type in {"auto", "telegram"}:
            resolved_type = await _detect_telegram_source_type(
                entity,
                auth_provider=auth_provider,
                auth_source_id=auth_source_id,
                telegram_client_factory=telegram_client_factory,
            )
        if resolved_type == "telegram_group":
            group_spec = TelegramGroupSpec(
                type="telegram_group",
                entity=entity,
                limit=limit,
                auth_source_id=auth_source_id,
            )
            return _with_source_name(group_spec, source_name, value, stripped, entity)
        channel_spec = TelegramChannelSpec(
            type="telegram_channel",
            entity=entity,
            limit=limit,
            auth_source_id=auth_source_id,
        )
        return _with_source_name(channel_spec, source_name, value, stripped, entity)

    parsed = urlsplit(stripped)
    if normalized_type in {"rss", "rss_feed"} and parsed.scheme in {"http", "https"}:
        rss_spec = RSSFeedSourceSpec.model_validate({"type": "rss_feed", "feed_url": stripped})
        return _with_source_name(rss_spec, source_name, value, stripped)

    if normalized_type in {"auto", "career_site", "site", "url"} and parsed.scheme in {
        "http",
        "https",
    }:
        site_spec = CareerSiteSpec(
            type="career_site",
            url=stripped,
            limit=limit,
            monitor="auto",
        )
        if runtime_defaults_fn is not None:
            site_spec = runtime_defaults_fn(site_spec)
        return _with_source_name(site_spec, source_name, value, stripped)

    msg = f"Unsupported source input: {value}"
    raise ValueError(msg)
