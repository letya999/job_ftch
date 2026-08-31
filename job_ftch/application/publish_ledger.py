from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol, cast
from urllib.parse import urlsplit, urlunsplit

if TYPE_CHECKING:
    from collections.abc import Awaitable


class RunStateStore(Protocol):
    """Smallest store contract required by the bot publication ledger."""

    async def get_run_state(self, key: str) -> str | None: ...

    async def set_run_state(self, key: str, value: str) -> None: ...


_PUBLISH_LEDGER_KEY = "bot_publish:sent_ids"
_PUBLISH_URL_LEDGER_KEY = "bot_publish:sent_urls"
# Keep enough history to cover the production channel instead of forgetting
# older URLs and re-publishing them after a restart or data migration.
_PUBLISH_LEDGER_LIMIT = 10_000


async def _maybe_await(value: object) -> object:
    if hasattr(value, "__await__"):
        return await cast("Awaitable[Any]", value)
    return value


def extract_publish_job_id(job: object) -> str | None:
    group_id = getattr(job, "group_id", None)
    if isinstance(group_id, str) and group_id:
        return group_id
    metadata = getattr(job, "metadata", None)
    if isinstance(metadata, dict):
        metadata_group_id = metadata.get("group_id")
        if isinstance(metadata_group_id, str) and metadata_group_id:
            return metadata_group_id
    for attr_name in ("job_id", "stable_id"):
        value = getattr(job, attr_name, None)
        if isinstance(value, str) and value:
            return value
    return None


def normalize_publish_url(url: str) -> str | None:
    text = str(url).strip()
    if not text:
        return None
    parts = urlsplit(text)
    if not parts.scheme and not parts.netloc:
        return text.casefold() or None
    normalized = urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path or "",
            parts.query,
            "",
        )
    )
    return normalized or None


def extract_publish_canonical_url(job: object) -> str | None:
    raw = getattr(job, "canonical_url", None)
    if raw is None:
        metadata = getattr(job, "metadata", None)
        if isinstance(metadata, dict):
            raw = metadata.get("canonical_url")
    if raw is None:
        return None
    if not isinstance(raw, str):
        raw = str(raw)
    return normalize_publish_url(raw)


async def _load_string_list(store: RunStateStore, key: str) -> list[str]:
    raw_obj = await _maybe_await(store.get_run_state(key))
    if not raw_obj:
        return []
    if not isinstance(raw_obj, (str, bytes, bytearray)):
        return []
    try:
        payload = json.loads(raw_obj)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, str) and item]


async def _persist_string_list(store: RunStateStore, key: str, ledger: list[str]) -> list[str]:
    trimmed = ledger[-_PUBLISH_LEDGER_LIMIT:]
    await _maybe_await(store.set_run_state(key, json.dumps(trimmed)))
    return trimmed


async def load_publish_ledger(store: RunStateStore) -> list[str]:
    return await _load_string_list(store, _PUBLISH_LEDGER_KEY)


async def persist_publish_ledger(store: RunStateStore, ledger: list[str]) -> list[str]:
    return await _persist_string_list(store, _PUBLISH_LEDGER_KEY, ledger)


async def load_publish_url_ledger(store: RunStateStore) -> list[str]:
    return await _load_string_list(store, _PUBLISH_URL_LEDGER_KEY)


async def persist_publish_url_ledger(store: RunStateStore, ledger: list[str]) -> list[str]:
    return await _persist_string_list(store, _PUBLISH_URL_LEDGER_KEY, ledger)
