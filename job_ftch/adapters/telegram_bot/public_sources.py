"""Public read-only source registry HTTP routes for the bot bridge.

Exposes sanitized runtime source state for allowlisted tenant slugs only.
Does not require the bridge API key. Never falls back to fixtures.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import time
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

import httpx
import structlog

from job_ftch.application.public_source_registry import (
    DEFAULT_PUBLIC_TENANT_ALLOWLIST,
    is_public_tenant,
    public_registry_error,
)

try:
    from fastapi import HTTPException, Request
except ImportError:  # pragma: no cover - api extra optional at import time
    HTTPException = None  # type: ignore[misc, assignment]
    Request = None  # type: ignore[misc, assignment]

if TYPE_CHECKING:
    from job_ftch.application.tenant_runner import TenantRunner
    from job_ftch.domain.public_source_registry import PublicSourceRegistry

logger = structlog.get_logger(__name__)

DEFAULT_PUBLIC_REGISTRY_CACHE_TTL_SECONDS = 30.0
_SOURCE_META: dict[str, tuple[str, str]] = {}


class _PageMetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.description = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value for key, value in attrs}
        if tag == "title":
            self._in_title = True
        if tag == "meta" and (values.get("name") or "").lower() in {
            "description",
            "og:description",
        }:
            self.description = values.get("content") or self.description
        if tag == "meta" and (values.get("property") or "").lower() == "og:description":
            self.description = values.get("content") or self.description

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def _clean_text(value: str, limit: int) -> str:
    return re.sub(r"\s+", " ", value).strip()[:limit]


def _fallback_source_meta(url: str, kind: str) -> tuple[str, str]:
    parts = urlsplit(url)
    host = (parts.hostname or "source").removeprefix("www.")
    path = unquote(parts.path).strip("/")
    if host == "t.me" and path:
        handle = path.split("/")[-1]
        label = "Telegram group" if kind == "telegram_group" else "Telegram channel"
        return f"@{handle}", f"{label} @{handle}"
    name = host.split(".")[0].replace("-", " ").replace("_", " ").title()
    return name or host, f"Vacancy source at {host}{('/' + path) if path else ''}"


def _safe_public_url(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.hostname == "localhost":
        return False
    try:
        return not ipaddress.ip_address(parts.hostname).is_private
    except ValueError:
        return True


async def _source_meta(client: httpx.AsyncClient, url: str, kind: str) -> tuple[str, str]:
    cached = _SOURCE_META.get(url)
    if cached is not None:
        return cached
    fallback = _fallback_source_meta(url, kind)
    if not _safe_public_url(url):
        return fallback
    try:
        response = await client.get(url, headers={"User-Agent": "job_ftch-source-registry/1"})
        response.raise_for_status()
        parser = _PageMetaParser()
        parser.feed(response.text[:1_000_000])
        result = (
            _clean_text(parser.title, 120) or fallback[0],
            _clean_text(parser.description, 240) or fallback[1],
        )
    except Exception:  # Metadata is best-effort; DNS/test guards must not break the registry.
        result = fallback
    _SOURCE_META[url] = result
    return result


async def _enrich_sources(sources: list[dict[str, Any]]) -> None:
    semaphore = asyncio.Semaphore(12)
    async with httpx.AsyncClient(timeout=4.0, follow_redirects=False) as client:

        async def enrich(source: dict[str, Any]) -> None:
            url = str(source.get("public_url") or "")
            if not url:
                source["display_name"] = str(
                    source.get("public_name") or source.get("source_id") or "Source"
                )
                source["description"] = "Configured vacancy source"
                return
            async with semaphore:
                name, description = await _source_meta(
                    client, url, str(source.get("kind") or "source")
                )
            source["display_name"] = name
            source["description"] = description

        await asyncio.gather(*(enrich(source) for source in sources))


class PublicRegistryCache:
    """Tiny process-local TTL cache for public registry payloads."""

    def __init__(self, ttl_seconds: float = DEFAULT_PUBLIC_REGISTRY_CACHE_TTL_SECONDS) -> None:
        self._ttl = max(0.0, float(ttl_seconds))
        self._entries: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        if self._ttl <= 0:
            return None
        item = self._entries.get(key)
        if item is None:
            return None
        expires_at, payload = item
        if time.monotonic() >= expires_at:
            self._entries.pop(key, None)
            return None
        return payload

    def set(self, key: str, payload: dict[str, Any]) -> None:
        if self._ttl <= 0:
            return
        self._entries[key] = (time.monotonic() + self._ttl, payload)

    def clear(self) -> None:
        self._entries.clear()


async def build_public_sources_response(
    runner: TenantRunner,
    tenant_slug: str,
    *,
    allowlist: frozenset[str] | None = None,
    cache: PublicRegistryCache | None = None,
    enrich: bool = False,
) -> dict[str, Any]:
    """Resolve a public registry payload or raise HTTPException-compatible errors.

    Returns a JSON-ready dict. Raises ``LookupError`` when the tenant is not
    published (caller maps that to HTTP 404 without leaking private tenants).
    """
    resolved_allowlist = allowlist if allowlist is not None else DEFAULT_PUBLIC_TENANT_ALLOWLIST
    slug = tenant_slug.strip()
    if not slug or not is_public_tenant(slug, allowlist=resolved_allowlist):
        raise LookupError("public source registry not available")

    if cache is not None:
        cached = cache.get(slug)
        if cached is not None:
            logger.info(
                "public_source_registry_cache_hit",
                tenant_slug=slug,
                source_count=cached.get("source_count"),
            )
            return cached

    try:
        registry: PublicSourceRegistry = await runner.list_public_sources(
            slug,
            allowlist=resolved_allowlist,
        )
    except Exception as exc:  # noqa: BLE001 - public boundary
        logger.warning(
            "public_source_registry_failed",
            tenant_slug=slug,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        registry = public_registry_error(
            tenant_slug=slug,
            message="runtime source listing failed",
            status="error",
        )

    if registry.status == "error" and registry.error == "tenant not found":
        raise LookupError("public source registry not available")

    payload = registry.model_dump(mode="json")
    if enrich:
        await _enrich_sources(payload.get("sources", []))
    if cache is not None and registry.status == "ok":
        cache.set(slug, payload)
    logger.info(
        "public_source_registry_served",
        tenant_slug=slug,
        status=registry.status,
        source_count=registry.source_count,
        stale=registry.stale,
    )
    return payload


def mount_public_source_routes(
    app: Any,
    runner: TenantRunner,
    *,
    allowlist: frozenset[str] | None = None,
    cache_ttl_seconds: float = DEFAULT_PUBLIC_REGISTRY_CACHE_TTL_SECONDS,
    limiter: Any | None = None,
) -> PublicRegistryCache:
    """Register public source registry routes on a FastAPI app.

    Returns the process-local cache so tests can clear it between cases.
    """
    if HTTPException is None or Request is None:
        msg = "FastAPI bridge requires the 'api' extra: pip install job-ftch[api]"
        raise ImportError(msg)

    cache = PublicRegistryCache(ttl_seconds=cache_ttl_seconds)
    resolved_allowlist = allowlist if allowlist is not None else DEFAULT_PUBLIC_TENANT_ALLOWLIST

    async def public_tenant_sources(request: Request, tenant_slug: str) -> dict[str, Any]:
        del request  # present for slowapi and ASGI parity
        try:
            return await build_public_sources_response(
                runner,
                tenant_slug,
                allowlist=resolved_allowlist,
                cache=cache,
                enrich=True,
            )
        except LookupError as exc:
            raise HTTPException(
                status_code=404,
                detail="Public source registry not available.",
            ) from exc

    handler: Any = public_tenant_sources
    if limiter is not None:
        handler = limiter.limit("30/minute")(public_tenant_sources)

    app.add_api_route(
        "/public/tenants/{tenant_slug}/sources.json",
        handler,
        methods=["GET"],
        name="public_tenant_sources",
        response_model=None,
    )
    app.add_api_route(
        "/public/tenants/{tenant_slug}/sources",
        handler,
        methods=["GET"],
        name="public_tenant_sources_alias",
        response_model=None,
    )
    return cache
