from __future__ import annotations

from paritylab.models import RequestRecord
from paritylab.protected_site import JobCatalog, classify_intent


def _request(path: str, *, query: str = "", seq: int) -> RequestRecord:
    return RequestRecord(
        request_id=f"req-{seq}",
        session_id="test-session",
        observed_at="2026-08-04T00:00:00.000+00:00",
        monotonic_ns=1_000_000_000 + seq * 250_000_000,
        method="GET",
        path=path,
        query=query,
        scheme="https",
        http_version="2",
        client_host="127.0.0.1",
        client_port=40000 + seq,
        connection_id=None,
        tls_ja3=None,
        tls_ja4=None,
        headers=(),
        response_status=200,
        response_headers=(),
        duration_ms=1.0,
    )


def test_catalog_is_deterministic() -> None:
    first = JobCatalog(seed=7)
    second = JobCatalog(seed=7)
    assert first.jobs() == second.jobs()
    assert first.sitemap_xml() == second.sitemap_xml()
    assert first.total_jobs == first.pages * first.jobs_per_page
    assert "Sitemap: /sitemap.xml" in first.robots_txt()
    assert "Disallow: /internal/" in first.robots_txt()
    listing = first.listing_html(1)
    assert "/trap/hot-content" in listing
    assert 'aria-hidden="true"' in listing


def test_listing_and_detail_render_only_known_jobs() -> None:
    catalog = JobCatalog(seed=3)
    job = catalog.jobs()[0]
    assert catalog.detail_path(job.job_id) in catalog.listing_html(job.page)
    assert job.title in catalog.detail_html(job.job_id)
    assert catalog.api_detail_json(job.job_id)["id"] == job.job_id
    assert catalog.api_listing_json(job.page)["items"][0]["id"] == job.job_id


def test_intent_api_harvest() -> None:
    catalog = JobCatalog(seed=5)
    requests = [_request("/api/jobs", query=f"page={page}", seq=seq) for seq, page in enumerate((1, 2, 3), start=1)]
    job_ids = [item.job_id for item in catalog.jobs()][:4]
    requests.extend(
        _request(f"/api/jobs/{job_id}", seq=4 + index) for index, job_id in enumerate(job_ids)
    )
    intent = classify_intent(requests, catalog)
    assert intent.intent == "api_harvest"
    assert intent.api_requests == 7
    assert intent.distinct_jobs == 4


def test_intent_pagination_walk() -> None:
    catalog = JobCatalog(seed=5)
    requests = [_request("/jobs", query=f"page={page}", seq=seq) for seq, page in enumerate((1, 2, 3, 4), start=1)]
    intent = classify_intent(requests, catalog)
    assert intent.intent == "pagination_walk"
    assert intent.listing_pages == 4


def test_intent_detail_harvest() -> None:
    catalog = JobCatalog(seed=5)
    requests = [
        _request(f"/jobs/{item.job_id}", seq=seq)
        for seq, item in enumerate(catalog.jobs()[:6], start=1)
    ]
    intent = classify_intent(requests, catalog)
    assert intent.intent == "detail_harvest"
    assert intent.coverage_ratio > 0


def test_intent_trap_seeker_wins() -> None:
    catalog = JobCatalog(seed=5)
    requests = [
        _request("/jobs", seq=1),
        _request("/trap/hot-content", seq=2),
        _request("/internal/pricing-dump", seq=3),
    ]
    intent = classify_intent(requests, catalog)
    assert intent.intent == "trap_seeker"
    assert intent.trap_hits == 2


def test_intent_single_page_and_probe_only() -> None:
    catalog = JobCatalog(seed=5)
    single = classify_intent([_request("/jobs", seq=1)], catalog)
    assert single.intent == "single_page_fetch"
    probe_only = classify_intent([_request("/", seq=1)], catalog)
    assert probe_only.intent == "probe_only"
    recon = classify_intent([_request("/robots.txt", seq=1), _request("/sitemap.xml", seq=2)], catalog)
    assert recon.intent == "recon_only"
