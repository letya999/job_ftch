"""Reachability checks for URL and Telegram sources before adding to tenant."""

from __future__ import annotations

import httpx
import structlog

logger = structlog.get_logger(__name__)


async def check_url_reachable(url: str, *, timeout: float = 10.0) -> tuple[bool, str]:
    """HEAD then GET check on a URL. Returns (ok, reason)."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            try:
                resp = await client.head(url)
            except Exception:
                resp = await client.get(url)
            if resp.status_code < 400:
                return True, ""
            return False, f"HTTP {resp.status_code}"
    except Exception as exc:
        return False, str(exc)


async def validate_sources(
    links: list[str],
    *,
    telegram_client: object | None = None,
) -> dict[str, tuple[bool, str]]:
    """Validate a list of source links. Returns {link: (ok, reason)}."""
    results: dict[str, tuple[bool, str]] = {}
    for link in links:
        is_tg = link.startswith("@") or "t.me/" in link or link.startswith("https://t.me")
        if is_tg and telegram_client is not None:
            try:
                entity = link.lstrip("@").replace("https://t.me/", "").replace("t.me/", "")
                # Telethon: try get_entity — if it raises, channel not accessible
                from telethon import TelegramClient as _TelegramClient

                if isinstance(telegram_client, _TelegramClient):
                    await telegram_client.get_entity(entity)
                results[link] = (True, "")
            except Exception as exc:
                results[link] = (False, str(exc))
        elif is_tg:
            # No Telegram client available — assume reachable (can't verify)
            results[link] = (True, "no_telegram_client")
        else:
            ok, reason = await check_url_reachable(link)
            results[link] = (ok, reason)
    return results
