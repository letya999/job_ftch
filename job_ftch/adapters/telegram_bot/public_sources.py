"""Public read-only source registry HTTP routes for the bot bridge.

Exposes sanitized runtime source state for allowlisted tenant slugs only.
Does not require the bridge API key. Never falls back to fixtures.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

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
