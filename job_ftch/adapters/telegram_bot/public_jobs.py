"""Public-safe published vacancy routes backed by the live TenantRunner."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

try:
    from fastapi import HTTPException, Query, Request
except ImportError:  # pragma: no cover - api extra optional at import time
    HTTPException = None  # type: ignore[misc, assignment]
    Query = None  # type: ignore[assignment]
    Request = None  # type: ignore[misc, assignment]

from job_ftch.application.public_source_registry import (
    DEFAULT_PUBLIC_TENANT_ALLOWLIST,
    is_public_tenant,
)
from job_ftch.application.publish_ledger import extract_publish_job_id

DEFAULT_PUBLIC_JOBS_CACHE_TTL_SECONDS = 600.0
MAX_PUBLIC_JOBS = 1000


class PublicJobsRunner(Protocol):
    async def latest_jobs(self, tenant_id: str, *, limit: int) -> Sequence[object]: ...


class PublicJobsCache:
    def __init__(self, ttl_seconds: float = DEFAULT_PUBLIC_JOBS_CACHE_TTL_SECONDS) -> None:
        self.ttl = max(0.0, float(ttl_seconds))
        self.entries: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        item = self.entries.get(key)
        if self.ttl <= 0 or item is None:
            return None
        if time.monotonic() >= item[0]:
            self.entries.pop(key, None)
            return None
        return item[1]

    def set(self, key: str, payload: dict[str, Any]) -> None:
        if self.ttl > 0:
            self.entries[key] = (time.monotonic() + self.ttl, payload)


def _public_url(value: object) -> str | None:
    text = str(value or "").strip()
    return text if text.startswith(("https://", "http://")) else None


def _public_job(job: object) -> dict[str, Any]:
    tags = [str(value) for value in (getattr(job, "tools_stack", ()) or ())[:8]]
    if not tags:
        tags = [str(value) for value in (getattr(job, "skills_explicit", ()) or ())[:8]]
    compensation = getattr(job, "compensation", None)
    location = getattr(job, "location", None)
    if not location:
        location = (
            ", ".join(
                dict.fromkeys(
                    str(value).strip()
                    for value in (
                        getattr(job, "city", None),
                        getattr(job, "region", None),
                        getattr(job, "country", None),
                    )
                    if value
                )
            )
            or None
        )
    posted_at = getattr(job, "posted_at", None)
    return {
        "id": extract_publish_job_id(job),
        "title": getattr(job, "title", None),
        "company": getattr(job, "company", None),
        "description": str(getattr(job, "description", None) or "")[:360],
        "location": location,
        "workMode": str(getattr(job, "work_mode", "unknown")),
        "seniority": str(getattr(job, "seniority", "unknown")),
        "tags": tags,
        "source": getattr(job, "source_name", None),
        "sourceKind": str(getattr(job, "source_kind", "unknown")),
        "postedAt": posted_at.isoformat() if posted_at is not None else None,
        "compensation": compensation.model_dump(mode="json") if compensation is not None else None,
        "url": _public_url(getattr(job, "canonical_url", None)),
    }


async def build_public_jobs_response(
    runner: PublicJobsRunner,
    tenant_slug: str,
    *,
    limit: int = 20,
    allowlist: frozenset[str] | None = None,
    cache: PublicJobsCache | None = None,
) -> dict[str, Any]:
    resolved = allowlist if allowlist is not None else DEFAULT_PUBLIC_TENANT_ALLOWLIST
    if not is_public_tenant(tenant_slug.strip(), allowlist=resolved):
        raise LookupError("public jobs not available")
    bounded_limit = min(max(int(limit), 1), MAX_PUBLIC_JOBS)
    key = f"{tenant_slug}:{bounded_limit}"
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            return cached
    candidates = await runner.latest_jobs(tenant_slug, limit=max(2000, bounded_limit))
    jobs = candidates[:bounded_limit]
    payload = {
        "generated_at": time.time(),
        "tenant_slug": tenant_slug,
        "job_count": len(jobs),
        "jobs": [_public_job(job) for job in jobs],
    }
    if cache is not None:
        cache.set(key, payload)
    return payload


def mount_public_job_routes(
    app: Any,
    runner: PublicJobsRunner,
    *,
    allowlist: frozenset[str] | None = None,
    cache_ttl_seconds: float = DEFAULT_PUBLIC_JOBS_CACHE_TTL_SECONDS,
    limiter: Any | None = None,
) -> PublicJobsCache:
    if HTTPException is None or Query is None or Request is None:
        raise ImportError("FastAPI bridge requires the 'api' extra: pip install job-ftch[api]")
    cache = PublicJobsCache(cache_ttl_seconds)
    resolved = allowlist if allowlist is not None else DEFAULT_PUBLIC_TENANT_ALLOWLIST

    async def public_tenant_jobs(
        request: Request, tenant_slug: str, limit: int = Query(100, ge=1, le=MAX_PUBLIC_JOBS)
    ) -> dict[str, Any]:
        del request
        try:
            return await build_public_jobs_response(
                runner, tenant_slug, limit=limit, allowlist=resolved, cache=cache
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Public jobs not available.") from exc

    handler: Any = public_tenant_jobs
    if limiter is not None:
        handler = limiter.limit("30/minute")(public_tenant_jobs)
    for suffix, name in (("jobs.json", "public_tenant_jobs"), ("jobs", "public_tenant_jobs_alias")):
        app.add_api_route(
            f"/public/tenants/{{tenant_slug}}/{suffix}",
            handler,
            methods=["GET"],
            name=name,
            response_model=None,
        )
    return cache
