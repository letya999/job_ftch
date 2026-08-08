"""Deterministic owned "career site" content model and scrape-intent classifier.

The playground serves a fully local, seeded job-listing site so the detector can
answer not only "is this a bot?" but also "what is this client trying to parse?".
No external content, provider script, or third-party target is involved.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any

from paritylab.models import IntentReport, JsonValue, RequestRecord

_TITLE_ROLES = (
    "Backend Engineer",
    "Data Analyst",
    "DevOps Engineer",
    "Frontend Engineer",
    "ML Engineer",
    "Product Manager",
    "QA Engineer",
    "Support Specialist",
)
_TITLE_LEVELS = ("Junior", "Middle", "Senior", "Lead")
_COMPANIES = (
    "Astra Telecom",
    "Beta Systems",
    "Core Networks",
    "Delta Soft",
    "Echo Digital",
    "Foxtrot Labs",
)
_TRAP_PATHS = ("/trap/hot-content", "/internal/pricing-dump")


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class Job:
    job_id: str
    title: str
    company: str
    page: int


@dataclass(frozen=True, slots=True)
class JobCatalog:
    seed: int = 20260804
    pages: int = 4
    jobs_per_page: int = 6

    def __post_init__(self) -> None:
        if self.pages < 1 or self.jobs_per_page < 1:
            raise ValueError("catalog must contain at least one job")

    @property
    def total_jobs(self) -> int:
        return self.pages * self.jobs_per_page

    def jobs(self) -> tuple[Job, ...]:
        output: list[Job] = []
        for index in range(self.total_jobs):
            role = _TITLE_ROLES[(self.seed + index) % len(_TITLE_ROLES)]
            level = _TITLE_LEVELS[(self.seed + index * 3) % len(_TITLE_LEVELS)]
            company = _COMPANIES[(self.seed + index * 7) % len(_COMPANIES)]
            output.append(
                Job(
                    job_id=f"job-{index + 1:04d}",
                    title=f"{level} {role}",
                    company=company,
                    page=index // self.jobs_per_page + 1,
                )
            )
        return tuple(output)

    def job(self, job_id: str) -> Job | None:
        return next((item for item in self.jobs() if item.job_id == job_id), None)

    @property
    def job_ids(self) -> frozenset[str]:
        return frozenset(item.job_id for item in self.jobs())

    @staticmethod
    def listing_path(page: int) -> str:
        return "/jobs" if page <= 1 else f"/jobs?page={page}"

    @staticmethod
    def detail_path(job_id: str) -> str:
        return f"/jobs/{job_id}"

    @staticmethod
    def api_listing_path(page: int) -> str:
        return f"/api/jobs?page={page}"

    @staticmethod
    def api_detail_path(job_id: str) -> str:
        return f"/api/jobs/{job_id}"

    def robots_txt(self) -> str:
        return (
            "User-agent: *\n"
            "Allow: /jobs\n"
            "Disallow: /internal/\n"
            f"Sitemap: /sitemap.xml\n"
        )

    def sitemap_xml(self) -> str:
        urls = ["/jobs"]
        urls.extend(self.listing_path(page) for page in range(2, self.pages + 1))
        urls.extend(self.detail_path(item.job_id) for item in self.jobs())
        body = "".join(f"<url><loc>https://localhost{path}</loc></url>" for path in urls)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'
        )

    def listing_html(self, page: int) -> str:
        if page < 1 or page > self.pages:
            raise ValueError("page out of range")
        rows = [
            item
            for item in self.jobs()
            if item.page == page
        ]
        links = "".join(
            f'<li><a href="{self.detail_path(item.job_id)}">{item.title}</a>'
            f" <span>{item.company}</span></li>"
            for item in rows
        )
        pagination = "".join(
            f'<a href="{self.listing_path(target)}">{target}</a>'
            for target in range(1, self.pages + 1)
            if target != page
        )
        honeypots = "".join(
            f'<a href="{path}" tabindex="-1" aria-hidden="true" '
            'style="position:absolute;left:-10000px;top:auto;width:1px;height:1px;overflow:hidden">'
            f"mirror {index}</a>"
            for index, path in enumerate(_TRAP_PATHS)
        )
        return (
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<title>Careers page {page}</title></head><body>"
            f"<h1>Careers — page {page}</h1>"
            f"<ul class=\"vacancies\">{links}</ul>"
            f"<nav class=\"pagination\">{pagination}</nav>"
            f"<div aria-hidden=\"true\">{honeypots}</div>"
            "</body></html>"
        )

    def detail_html(self, job_id: str) -> str:
        item = self.job(job_id)
        if item is None:
            raise ValueError("unknown job id")
        return (
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<title>{item.title} at {item.company}</title></head><body>"
            f"<h1>{item.title}</h1><p>Company: {item.company}</p>"
            f"<p>Requisition: {item.job_id}</p>"
            "<form action=\"/api/jobs/apply\" method=\"post\">"
            "<input name=\"resume\" type=\"file\">"
            "<input name=\"website\" tabindex=\"-1\" autocomplete=\"off\" "
            'style="position:absolute;left:-10000px" aria-hidden="true">'
            "<button type=\"submit\">Apply</button></form>"
            "</body></html>"
        )

    def api_listing_json(self, page: int) -> dict[str, Any]:
        if page < 1 or page > self.pages:
            raise ValueError("page out of range")
        return {
            "page": page,
            "pages": self.pages,
            "items": [
                {
                    "id": item.job_id,
                    "title": item.title,
                    "company": item.company,
                    "url": self.detail_path(item.job_id),
                    "digest": _stable_id(self.seed_str, item.job_id),
                }
                for item in self.jobs()
                if item.page == page
            ],
        }

    def api_detail_json(self, job_id: str) -> dict[str, Any]:
        item = self.job(job_id)
        if item is None:
            raise ValueError("unknown job id")
        return {
            "id": item.job_id,
            "title": item.title,
            "company": item.company,
            "page": item.page,
            "digest": _stable_id(self.seed_str, job_id),
        }

    @property
    def seed_str(self) -> str:
        return str(self.seed)

    def trap_paths(self) -> tuple[str, ...]:
        return _TRAP_PATHS


_PAGE_QUERY_RE = re.compile(r"(?:^|&)page=(\d+)(?:&|$)")
_DETAIL_PATH_RE = re.compile(r"^/jobs/([a-z0-9-]{1,64})$")
_API_DETAIL_PATH_RE = re.compile(r"^/api/jobs/([a-z0-9-]{1,64})$")


def _page_of(path: str, query: str) -> int | None:
    if path == "/jobs" and not query:
        return 1
    match = _PAGE_QUERY_RE.search(query or "")
    if match:
        return int(match.group(1))
    return None


def _classify_surface(path: str, query: str, catalog: JobCatalog) -> str:
    if path in _TRAP_PATHS or path.startswith("/trap/") or path.startswith("/internal/"):
        return "trap"
    if path == "/robots.txt":
        return "robots"
    if path == "/sitemap.xml":
        return "sitemap"
    if path.startswith("/api/jobs"):
        detail = _API_DETAIL_PATH_RE.match(path)
        if detail:
            return "api_detail" if detail.group(1) in catalog.job_ids else "api_unknown"
        return "api_listing" if _page_of("/jobs", query) is not None else "api_unknown"
    detail = _DETAIL_PATH_RE.match(path)
    if detail:
        return "detail" if detail.group(1) in catalog.job_ids else "unknown"
    if path == "/jobs":
        return "listing"
    return "other"


def classify_intent(requests: Sequence[RequestRecord], catalog: JobCatalog) -> IntentReport:
    """Classify what the client is trying to parse from the protected site."""
    surfaces: dict[str, int] = {}
    listing_pages: set[int] = set()
    job_ids: set[str] = set()
    trap_hits = 0
    content_timestamps: list[int] = []
    sample_paths: list[str] = []

    ordered = sorted(requests, key=lambda item: item.monotonic_ns)
    for request in ordered:
        surface = _classify_surface(request.path, request.query, catalog)
        surfaces[surface] = surfaces.get(surface, 0) + 1
        if surface in {"listing", "api_listing"}:
            page = _page_of(request.path, request.query)
            if page is not None:
                listing_pages.add(page)
        elif surface == "detail":
            match = _DETAIL_PATH_RE.match(request.path)
            if match:
                job_ids.add(match.group(1))
        elif surface == "api_detail":
            match = _API_DETAIL_PATH_RE.match(request.path)
            if match:
                job_ids.add(match.group(1))
        elif surface == "trap":
            trap_hits += 1
        if surface not in {"robots", "sitemap", "other", "unknown", "api_unknown"}:
            content_timestamps.append(request.monotonic_ns)
            if len(sample_paths) < 24:
                sample_paths.append(request.path + (f"?{request.query}" if request.query else ""))

    api_requests = surfaces.get("api_listing", 0) + surfaces.get("api_detail", 0)
    content_requests = sum(
        count
        for surface, count in surfaces.items()
        if surface in {"listing", "api_listing", "detail", "api_detail"}
    )
    coverage = len(job_ids) / catalog.total_jobs if catalog.total_jobs else 0.0

    gaps_ms = [
        (later - earlier) / 1_000_000
        for earlier, later in zip(content_timestamps, content_timestamps[1:], strict=False)
    ]
    span_seconds = (
        (content_timestamps[-1] - content_timestamps[0]) / 1_000_000_000
        if len(content_timestamps) > 1
        else 0.0
    )
    velocity = len(content_timestamps) / span_seconds if span_seconds > 0 else 0.0

    intent, confidence = _decide_intent(
        surfaces=surfaces,
        listing_pages=listing_pages,
        job_count=len(job_ids),
        api_requests=api_requests,
        content_requests=content_requests,
        trap_hits=trap_hits,
        coverage=coverage,
        catalog=catalog,
    )
    evidence: dict[str, JsonValue] = {
        "sample_paths": [str(item) for item in sample_paths],
        "listing_pages_seen": [int(item) for item in sorted(listing_pages)],
        "job_ids_sample": [str(item) for item in sorted(job_ids)[:24]],
    }
    return IntentReport(
        intent=intent,
        confidence=confidence,
        trap_hits=trap_hits,
        distinct_jobs=len(job_ids),
        listing_pages=len(listing_pages),
        api_requests=api_requests,
        coverage_ratio=round(coverage, 4),
        velocity_rps=round(velocity, 3),
        median_gap_ms=round(median(gaps_ms), 3) if gaps_ms else 0.0,
        surfaces={key: value for key, value in sorted(surfaces.items())},
        evidence=evidence,
    )


def _decide_intent(
    *,
    surfaces: dict[str, int],
    listing_pages: set[int],
    job_count: int,
    api_requests: int,
    content_requests: int,
    trap_hits: int,
    coverage: float,
    catalog: JobCatalog,
) -> tuple[str, float]:
    del catalog
    if trap_hits > 0:
        return "trap_seeker", 0.95
    if content_requests == 0:
        if surfaces.get("sitemap") or surfaces.get("robots"):
            return "recon_only", 0.7
        return "probe_only", 0.6
    api_ratio = api_requests / content_requests if content_requests else 0.0
    if api_ratio >= 0.6 and api_requests >= 3:
        return "api_harvest", 0.9
    if job_count >= 5 or coverage >= 0.3:
        return "detail_harvest", 0.85
    if len(listing_pages) >= 3 and sorted(listing_pages) == list(
        range(min(listing_pages), min(listing_pages) + len(listing_pages))
    ):
        return "pagination_walk", 0.85
    if surfaces.get("sitemap") and content_requests >= 3:
        return "full_crawl", 0.8
    if content_requests == 1:
        return "single_page_fetch", 0.75
    return "mixed_crawl", 0.5


def iter_trap_paths() -> Iterable[str]:
    return _TRAP_PATHS
