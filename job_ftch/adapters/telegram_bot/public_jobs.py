"""Public-safe published vacancy routes backed by the live TenantRunner."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Sequence

try:
    from fastapi import HTTPException, Query, Request
except ImportError:  # pragma: no cover - api extra optional at import time
    HTTPException = None  # type: ignore[misc, assignment]
    Query = None  # type: ignore[assignment]
    Request = None  # type: ignore[misc, assignment]

from job_ftch.application.compensation import public_compensation
from job_ftch.application.public_source_registry import (
    DEFAULT_PUBLIC_TENANT_ALLOWLIST,
    is_public_tenant,
)
from job_ftch.application.publication_audience import (
    evaluate_audience,
    policy_for,
    publication_facts,
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
    tags = [
        str(getattr(value, "canonical_name", value)).strip()
        for value in (getattr(job, "tools_stack", ()) or ())[:8]
    ]
    if not tags:
        tags = [
            str(getattr(value, "canonical_name", value)).strip()
            for value in (getattr(job, "skills_explicit", ()) or ())[:8]
        ]
    compensation = public_compensation(job)
    facts = publication_facts(job)
    posted_at = getattr(job, "posted_at", None)
    return {
        "id": extract_publish_job_id(job),
        "title": getattr(job, "title", None),
        "company": getattr(job, "company", None),
        "description": str(getattr(job, "description", None) or "")[:360],
        "location": facts.location,
        "city": facts.city,
        "country": facts.country,
        "countries": list(facts.hiring_countries),
        "regions": list(facts.regions),
        "roleFamily": getattr(job, "role_family", None),
        "profile": facts.role,
        "profiles": list(facts.roles),
        "grades": list(facts.grades),
        "postingLanguages": list(facts.languages),
        "workMode": str(getattr(job, "work_mode", "unknown")),
        "seniority": facts.grade or "unknown",
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
    get_runtime = getattr(runner, "get_runtime", None)
    runtime = get_runtime(tenant_slug) if callable(get_runtime) else None
    policy = policy_for(runtime, "site")
    key = f"{tenant_slug}:site:{policy.version}" if policy else tenant_slug
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            jobs = cached["jobs"][:bounded_limit]
            return {**cached, "jobs": jobs, "job_count": len(jobs)}
    fetch_limit = MAX_PUBLIC_JOBS if cache is not None else bounded_limit
    group_store = getattr(runtime, "job_group_store", None)
    list_public = getattr(group_store, "list_public_canonical_jobs", None)
    if callable(list_public):
        candidates = cast(
            "Sequence[object]",
            await list_public(fetch_limit, policy=policy)
            if policy
            else await list_public(fetch_limit),
        )
    else:
        pool = max(2000, fetch_limit)
        while True:
            batch = await runner.latest_jobs(tenant_slug, limit=pool)
            candidates = (
                [
                    job
                    for job in batch
                    if evaluate_audience(job, policy).status == "allow" or policy.shadow
                ]
                if policy
                else batch
            )
            if len(candidates) >= fetch_limit or len(batch) < pool:
                break
            pool *= 2
    jobs = candidates[:fetch_limit]
    public_jobs = [_public_job(job) for job in jobs]
    payload = {
        "generated_at": time.time(),
        "tenant_slug": tenant_slug,
        "job_count": len(jobs),
        "jobs": public_jobs,
    }
    if cache is not None:
        cache.set(key, payload)
    selected = public_jobs[:bounded_limit]
    return {**payload, "jobs": selected, "job_count": len(selected)}


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
