"""Concrete source reachability probes owned by the infrastructure layer."""

from __future__ import annotations

from typing import Protocol

import httpx
import structlog

from job_ftch.infrastructure.network.ssrf_guard import SSRFGuardedTransport

logger = structlog.get_logger(__name__)


class TelegramEntityClient(Protocol):
    async def get_entity(self, entity: str) -> object: ...


async def check_url_reachable(url: str, *, timeout: float = 10.0) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            transport=SSRFGuardedTransport(httpx.AsyncHTTPTransport()),
        ) as client:
            try:
                response = await client.head(url)
            except Exception:
                response = await client.get(url)
            ok = response.status_code < 400
            return ok, "" if ok else f"HTTP {response.status_code}"
    except Exception as exc:
        return False, str(exc)


async def validate_sources(
    links: list[str],
    *,
    telegram_client: TelegramEntityClient | None = None,
) -> dict[str, tuple[bool, str]]:
    results: dict[str, tuple[bool, str]] = {}
    for link in links:
        is_telegram = link.startswith("@") or "t.me/" in link
        if is_telegram and telegram_client is not None:
            try:
                entity = link.lstrip("@").replace("https://t.me/", "").replace("t.me/", "")
                await telegram_client.get_entity(entity)
                results[link] = (True, "")
            except Exception as exc:
                results[link] = (False, str(exc))
        elif is_telegram:
            results[link] = (True, "no_telegram_client")
        else:
            results[link] = await check_url_reachable(link)
    return results
