"""RSS feed source. Requires feedparser (pip install feedparser) — optional dep."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

import httpx
import structlog

from job_ftch.application.registry import register_source_spec
from job_ftch.application.watermark import IncrementalCursor
from job_ftch.domain import RawItem, SourceKind

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.application.contracts import AuthProvider, Store, StoreConnector
    from job_ftch.domain import QuarantinedRawItem
    from job_ftch.domain.source_spec import RSSFeedSourceSpec

logger = structlog.get_logger(__name__)

try:
    import feedparser

    _FEEDPARSER_AVAILABLE = True
except ImportError:
    feedparser = None  # type: ignore[assignment]
    _FEEDPARSER_AVAILABLE = False


class RSSFeedSource:
    """HTTP-poll RSS/Atom feed. Incremental: skips already-seen entry IDs."""

    def __init__(
        self,
        spec: RSSFeedSourceSpec,
        auth: AuthProvider,
        store: Store | None = None,
    ) -> None:
        self.spec = spec
        self.auth = auth
        self.store = store
        self.source_name = spec.source_name or str(spec.feed_url)
        self._cursor_source_id = f"rss:{self.source_name}"

    async def fetch(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        if not _FEEDPARSER_AVAILABLE:
            raise ImportError(
                "feedparser is required for RSS sources. Install with: uv add feedparser"
            )

        feed_url = str(self.spec.feed_url)

        # Load seen IDs from store for incremental dedup
        seen_ids: set[str] = set()
        if self.spec.incremental and self.store:
            raw = await IncrementalCursor(cast("StoreConnector", self.store)).get(
                self._cursor_source_id
            )
            if raw:
                seen_ids = set(raw.split(","))

        # Fetch
        headers: dict[str, str] = {}
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(feed_url, headers=headers)
                response.raise_for_status()
                raw_content = response.text
            except Exception:
                logger.exception("rss_fetch_failed", url=feed_url)
                raise

        # Parse in thread pool (feedparser is sync and CPU-bound)
        loop = asyncio.get_running_loop()
        feed = await loop.run_in_executor(None, feedparser.parse, raw_content)

        new_ids: list[str] = []
        for entry in feed.entries:
            entry_id = entry.get("id") or entry.get("link") or ""
            if self.spec.incremental and entry_id in seen_ids:
                continue

            url = entry.get("link") or entry.get("url") or ""
            title = entry.get("title") or ""
            summary = entry.get("summary") or entry.get("description") or ""
            text = f"{title}\n{summary}".strip() or str(entry)
            url_str = str(url) if url else None

            yield RawItem(
                source_kind=SourceKind.CAREER_SITE,
                source_name=self.source_name,
                external_id=entry_id,
                url=url_str,  # type: ignore[arg-type]
                text=text,
                metadata={"title": title, "summary": summary, "entry_id": entry_id},
            )

            if entry_id:
                new_ids.append(entry_id)

        # Persist seen IDs
        if self.spec.incremental and self.store and new_ids:
            all_seen = seen_ids | set(new_ids)
            # Keep only last 10000 to avoid unbounded growth
            trimmed = set(list(all_seen)[-10000:])
            await IncrementalCursor(cast("StoreConnector", self.store)).set(
                self._cursor_source_id, ",".join(trimmed)
            )


@register_source_spec("rss_feed")
def _create_rss(spec: Any, auth: AuthProvider, store: Any = None) -> RSSFeedSource:
    return RSSFeedSource(spec, auth)
