"""Telegram source adapters for channels, groups, and channel comments."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from job_ftch.application.registry import register_source, register_source_spec
from job_ftch.domain import RawItem, SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.application.contracts import AuthProvider
    from job_ftch.config import Settings
    from job_ftch.domain.source_spec import (
        TelegramChannelSpec,
        TelegramCommentsSpec,
        TelegramGroupSpec,
    )


class TelegramClientLike(Protocol):
    async def get_entity(self, entity: object) -> object:
        """Resolve a Telegram entity into a reusable chat object."""

    def iter_messages(
        self,
        entity: object,
        *,
        limit: int | None = None,
        reply_to: int | None = None,
        wait_time: float | None = None,
    ) -> AsyncIterator[object]:
        """Iterate over Telegram messages."""


def _get_attr(target: object, name: str) -> Any:
    return getattr(target, name, None)


def _display_name(sender: object | None) -> str | None:
    if sender is None:
        return None
    title = _get_attr(sender, "title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    parts = [
        value.strip()
        for value in (
            _get_attr(sender, "first_name"),
            _get_attr(sender, "last_name"),
        )
        if isinstance(value, str) and value.strip()
    ]
    if parts:
        return " ".join(parts)
    username = _get_attr(sender, "username")
    if isinstance(username, str) and username.strip():
        return username.strip()
    return None


def _message_text(message: object) -> str | None:
    for field_name in ("message", "text", "raw_text"):
        value = _get_attr(message, field_name)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _normalize_public_handle(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().removeprefix("@").rstrip("/")
    if not normalized:
        return None
    if normalized.startswith("https://t.me/"):
        normalized = normalized.removeprefix("https://t.me/")
    elif normalized.startswith("http://t.me/"):
        normalized = normalized.removeprefix("http://t.me/")
    normalized = normalized.split("/", maxsplit=1)[0].strip()
    return normalized or None


def _chat_username(chat: object, public_handle: str | None = None) -> str | None:
    username = _get_attr(chat, "username")
    if isinstance(username, str) and username.strip():
        return username.strip()
    return _normalize_public_handle(public_handle)


def _source_name(chat: object, public_handle: str | None = None) -> str:
    username = _chat_username(chat, public_handle)
    if username is not None:
        return username
    title = _get_attr(chat, "title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    chat_id = _get_attr(chat, "id")
    return f"telegram-{chat_id}"


def _message_url(
    chat: object, message_id: int | None, public_handle: str | None = None
) -> str | None:
    username = _chat_username(chat, public_handle)
    if username is not None and message_id is not None:
        return f"https://t.me/{username}/{message_id}"
    return None


def _base_metadata(
    chat: object, message: object, public_handle: str | None = None
) -> dict[str, Any]:
    sender = _get_attr(message, "sender")
    reply_to = _get_attr(message, "reply_to")
    return {
        "chat_id": _get_attr(chat, "id"),
        "chat_title": _get_attr(chat, "title"),
        "chat_username": _chat_username(chat, public_handle),
        "message_id": _get_attr(message, "id"),
        "sender_id": _get_attr(message, "sender_id"),
        "sender_username": _get_attr(sender, "username"),
        "sender_display_name": _display_name(sender),
        "views": _get_attr(message, "views"),
        "forwards": _get_attr(message, "forwards"),
        "grouped_id": _get_attr(message, "grouped_id"),
        "reply_to_message_id": _get_attr(reply_to, "reply_to_msg_id"),
    }


@asynccontextmanager
async def _client_session(
    client: TelegramClientLike,
    *,
    own_client: bool,
) -> AsyncIterator[TelegramClientLike]:
    if own_client:
        async with client as managed_client:  # type: ignore[attr-defined]
            yield managed_client
        return
    yield client


def _message_to_raw_item(
    *,
    source_kind: SourceKind,
    chat: object,
    message: object,
    public_handle: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> RawItem | None:
    text = _message_text(message)
    message_id = _get_attr(message, "id")
    if text is None or message_id is None:
        return None
    metadata = _base_metadata(chat, message, public_handle)
    if extra_metadata:
        metadata.update(extra_metadata)
    created_at = _get_attr(message, "date")
    if created_at is not None and not isinstance(created_at, datetime):
        msg = "Telegram message date must be a datetime instance."
        raise TypeError(msg)
    return build_raw_item(
        source_kind=source_kind,
        source_name=_source_name(chat, public_handle),
        external_id=str(message_id),
        url=_message_url(chat, int(message_id), public_handle),
        text=text,
        created_at=created_at,
        metadata=metadata,
    )


async def _safe_iter_messages(
    client: Any,
    chat: object,
    limit: int,
    reply_to: int | None = None,
    min_jitter: float = 1.0,
    max_jitter: float = 4.0,
) -> AsyncIterator[object]:
    """Safe pagination fetching chunks with Jitter and FloodWaitError handling."""
    import asyncio
    import random

    from telethon import errors

    yielded = 0
    offset_id = 0

    while yielded < limit:
        chunk_size = min(100, limit - yielded)
        try:
            messages = await client.get_messages(
                chat,
                limit=chunk_size,
                offset_id=offset_id,
                reply_to=reply_to,
            )
            if not messages:
                break

            for msg in messages:
                yield msg
                yielded += 1
                offset_id = msg.id

            if len(messages) < chunk_size:
                break

            if yielded < limit:
                # Sleep before fetching the next chunk
                await asyncio.sleep(random.uniform(min_jitter, max_jitter))

        except errors.FloodWaitError as exc:
            logger.warning(
                "telegram_flood_wait_caught",
                chat=getattr(chat, "id", str(chat)),
                sleep_seconds=exc.seconds,
                yielded=yielded,
            )
            break
        except Exception as exc:
            if _is_skippable_comment_thread_error(exc):
                break
            raise


def _is_skippable_comment_thread_error(exc: Exception) -> bool:
    return exc.__class__.__name__ == "MsgIdInvalidError" and "GetRepliesRequest" in str(exc)


class TelegramChannelSource:
    def __init__(
        self,
        client: TelegramClientLike,
        channel: str,
        *,
        limit: int = 100,
        min_jitter: float = 1.0,
        max_jitter: float = 4.0,
        own_client: bool = False,
    ) -> None:
        self._client = client
        self._channel = channel
        self._limit = limit
        self._min_jitter = min_jitter
        self._max_jitter = max_jitter
        self._own_client = own_client

    async def fetch(self) -> AsyncIterator[RawItem]:
        async with _client_session(self._client, own_client=self._own_client) as client:
            chat = await client.get_entity(self._channel)
            async for message in _safe_iter_messages(
                client,
                chat,
                limit=self._limit,
                min_jitter=self._min_jitter,
                max_jitter=self._max_jitter,
            ):
                item = _message_to_raw_item(
                    source_kind=SourceKind.TELEGRAM_CHANNEL,
                    chat=chat,
                    message=message,
                    public_handle=self._channel,
                )
                if item is not None:
                    yield item


class TelegramGroupSource:
    def __init__(
        self,
        client: TelegramClientLike,
        group: str,
        *,
        limit: int = 100,
        min_jitter: float = 1.0,
        max_jitter: float = 4.0,
        own_client: bool = False,
    ) -> None:
        self._client = client
        self._group = group
        self._limit = limit
        self._min_jitter = min_jitter
        self._max_jitter = max_jitter
        self._own_client = own_client

    async def fetch(self) -> AsyncIterator[RawItem]:
        async with _client_session(self._client, own_client=self._own_client) as client:
            chat = await client.get_entity(self._group)
            async for message in _safe_iter_messages(
                client,
                chat,
                limit=self._limit,
                min_jitter=self._min_jitter,
                max_jitter=self._max_jitter,
            ):
                item = _message_to_raw_item(
                    source_kind=SourceKind.TELEGRAM_GROUP,
                    chat=chat,
                    message=message,
                    public_handle=self._group,
                )
                if item is not None:
                    yield item


class TelegramCommentSource:
    def __init__(
        self,
        client: TelegramClientLike,
        channel: str,
        *,
        post_limit: int = 20,
        comment_limit_per_post: int = 50,
        min_jitter: float = 1.0,
        max_jitter: float = 4.0,
        own_client: bool = False,
    ) -> None:
        self._client = client
        self._channel = channel
        self._post_limit = post_limit
        self._comment_limit_per_post = comment_limit_per_post
        self._min_jitter = min_jitter
        self._max_jitter = max_jitter
        self._own_client = own_client

    async def fetch(self) -> AsyncIterator[RawItem]:
        async with _client_session(self._client, own_client=self._own_client) as client:
            chat = await client.get_entity(self._channel)
            async for post in _safe_iter_messages(
                client,
                chat,
                limit=self._post_limit,
                min_jitter=self._min_jitter,
                max_jitter=self._max_jitter,
            ):
                post_id = _get_attr(post, "id")
                if not isinstance(post_id, int):
                    continue
                try:
                    async for comment in _safe_iter_messages(
                        client,
                        chat,
                        limit=self._comment_limit_per_post,
                        reply_to=post_id,
                        min_jitter=self._min_jitter,
                        max_jitter=self._max_jitter,
                    ):
                        item = _message_to_raw_item(
                            source_kind=SourceKind.TELEGRAM_COMMENT,
                            chat=chat,
                            message=comment,
                            public_handle=self._channel,
                            extra_metadata={
                                "post_message_id": post_id,
                                "post_url": _message_url(chat, post_id, self._channel),
                            },
                        )
                        if item is not None:
                            yield item
                except Exception as exc:
                    if _is_skippable_comment_thread_error(exc):
                        continue
                    raise


def _get_proxy_config(settings: Settings) -> dict[str, Any] | None:
    if (
        not settings.telegram_proxy_type
        or not settings.telegram_proxy_host
        or not settings.telegram_proxy_port
    ):
        return None

    proxy_dict = {
        "proxy_type": settings.telegram_proxy_type.lower(),
        "addr": settings.telegram_proxy_host,
        "port": settings.telegram_proxy_port,
    }
    if settings.telegram_proxy_username:
        proxy_dict["username"] = settings.telegram_proxy_username
    if settings.telegram_proxy_password:
        proxy_dict["password"] = settings.telegram_proxy_password
    return proxy_dict


def _build_telegram_client(settings: Settings) -> Any:
    if settings.telegram_api_id is None or settings.telegram_api_hash is None:
        msg = "Telegram sources require JOB_FTCH_TELEGRAM_API_ID and JOB_FTCH_TELEGRAM_API_HASH."
        raise ValueError(msg)
    if settings.telegram_entity is None:
        msg = "Telegram sources require JOB_FTCH_TELEGRAM_ENTITY."
        raise ValueError(msg)
    from telethon import TelegramClient

    settings.telegram_session_path.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(
        str(settings.telegram_session_path),
        settings.telegram_api_id,
        settings.telegram_api_hash,
        proxy=_get_proxy_config(settings),
        timeout=settings.telegram_timeout_seconds,
        request_retries=settings.telegram_request_retries,
        connection_retries=settings.telegram_connection_retries,
        retry_delay=settings.telegram_retry_delay_seconds,
    )
    client.flood_sleep_threshold = settings.telegram_flood_sleep_threshold_seconds
    return client


@register_source("telegram_channel")
def _build_telegram_channel_source(settings: Settings) -> TelegramChannelSource:
    return TelegramChannelSource(
        _build_telegram_client(settings),
        settings.telegram_entity or "",
        limit=settings.telegram_message_limit,
        min_jitter=settings.telegram_jitter_min_seconds,
        max_jitter=settings.telegram_jitter_max_seconds,
        own_client=True,
    )


@register_source("telegram_group")
def _build_telegram_group_source(settings: Settings) -> TelegramGroupSource:
    return TelegramGroupSource(
        _build_telegram_client(settings),
        settings.telegram_entity or "",
        limit=settings.telegram_message_limit,
        min_jitter=settings.telegram_jitter_min_seconds,
        max_jitter=settings.telegram_jitter_max_seconds,
        own_client=True,
    )


@register_source("telegram_comment")
def _build_telegram_comment_source(settings: Settings) -> TelegramCommentSource:
    return TelegramCommentSource(
        _build_telegram_client(settings),
        settings.telegram_entity or "",
        post_limit=settings.telegram_comment_post_limit,
        comment_limit_per_post=settings.telegram_comment_limit_per_post,
        min_jitter=settings.telegram_jitter_min_seconds,
        max_jitter=settings.telegram_jitter_max_seconds,
        own_client=True,
    )


def _build_client_v2(auth_id: str | None, auth: AuthProvider) -> Any:
    from pathlib import Path

    from telethon import TelegramClient

    from job_ftch.config import get_settings

    settings = get_settings()
    creds = auth.resolve(auth_id or "telegram")

    # Fallback to Settings-style env vars when AuthProvider returns nothing
    api_id_val = creds.get("api_id") or (
        str(settings.telegram_api_id) if settings.telegram_api_id else None
    )
    api_hash = creds.get("api_hash") or settings.telegram_api_hash

    if not api_id_val or not api_hash:
        msg = f"Telegram credentials (api_id, api_hash) missing for auth_source_id: {auth_id!r}"
        raise ValueError(msg)

    if not auth_id:
        # Reuse the configured session (already authenticated)
        session_path = Path(str(settings.telegram_session_path).removesuffix(".session"))
    else:
        session_path = Path(".runtime/telegram") / auth_id

    session_path.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(
        str(session_path),
        int(api_id_val),
        api_hash,
        proxy=_get_proxy_config(settings),
        timeout=settings.telegram_timeout_seconds,
        request_retries=settings.telegram_request_retries,
        connection_retries=settings.telegram_connection_retries,
        retry_delay=settings.telegram_retry_delay_seconds,
    )
    client.flood_sleep_threshold = settings.telegram_flood_sleep_threshold_seconds
    return client


@register_source_spec("telegram_channel")
def _build_telegram_channel_source_v2(
    spec: TelegramChannelSpec,
    auth: AuthProvider,
    store: Any = None,
) -> TelegramChannelSource:
    from job_ftch.config import get_settings

    settings = get_settings()
    return TelegramChannelSource(
        _build_client_v2(spec.auth_source_id, auth),
        spec.entity,
        limit=spec.limit or settings.telegram_channel_default_limit,
        min_jitter=settings.telegram_jitter_min_seconds,
        max_jitter=settings.telegram_jitter_max_seconds,
        own_client=True,
    )


@register_source_spec("telegram_group")
def _build_telegram_group_source_v2(
    spec: TelegramGroupSpec,
    auth: AuthProvider,
    store: Any = None,
) -> TelegramGroupSource:
    from job_ftch.config import get_settings

    settings = get_settings()
    return TelegramGroupSource(
        _build_client_v2(spec.auth_source_id, auth),
        spec.entity,
        limit=spec.limit or settings.telegram_group_default_limit,
        min_jitter=settings.telegram_jitter_min_seconds,
        max_jitter=settings.telegram_jitter_max_seconds,
        own_client=True,
    )


@register_source_spec("telegram_comments")
def _build_telegram_comments_source_v2(
    spec: TelegramCommentsSpec,
    auth: AuthProvider,
    store: Any = None,
) -> TelegramCommentSource:
    from job_ftch.config import get_settings

    settings = get_settings()
    return TelegramCommentSource(
        _build_client_v2(spec.auth_source_id, auth),
        spec.entity,
        post_limit=spec.post_limit or settings.telegram_comment_default_limit,
        comment_limit_per_post=spec.comment_limit_per_post
        or settings.telegram_comment_limit_per_post,
        min_jitter=settings.telegram_jitter_min_seconds,
        max_jitter=settings.telegram_jitter_max_seconds,
        own_client=True,
    )
