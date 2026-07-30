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
            return group_spec.model_copy(update={"source_name": source_spec_name(group_spec)})
        channel_spec = TelegramChannelSpec(
            type="telegram_channel",
            entity=entity,
            limit=limit,
            auth_source_id=auth_source_id,
        )
        return channel_spec.model_copy(update={"source_name": source_spec_name(channel_spec)})

    parsed = urlsplit(stripped)
    if normalized_type in {"rss", "rss_feed"} and parsed.scheme in {"http", "https"}:
        rss_spec = RSSFeedSourceSpec.model_validate({"type": "rss_feed", "feed_url": stripped})
        return rss_spec.model_copy(update={"source_name": source_spec_name(rss_spec)})

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
        return site_spec.model_copy(update={"source_name": source_spec_name(site_spec)})

    msg = f"Unsupported source input: {value}"
    raise ValueError(msg)
