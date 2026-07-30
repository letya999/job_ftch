from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from job_ftch.infrastructure.sources.monitors.dom import (
    _has_board_gone_message,
    _has_confirmed_empty_board_message,
)
from job_ftch.infrastructure.sources.monitors.nextdata import (
    _build_url,
    _find_jobs_path,
    can_handle,
    discover,
)
from job_ftch.infrastructure.sources.monitors.shared import is_board_gone, is_soft_404


class _Response:
    status_code = 200

    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class _Client:
    def __init__(self, text: str) -> None:
        self._text = text

    async def get(self, url: str, **kwargs: object) -> _Response:
        del url, kwargs
        return _Response(self._text)


def _listing_html() -> str:
    path = (
        Path(__file__).parents[3]
        / "fixtures"
        / "real_world"
        / "monitors"
        / "nextdata"
        / "listing.html"
    )
    return path.read_text(encoding="utf-8")


def test_nextdata_helpers_build_urls_and_find_job_arrays() -> None:
    item = {"id": "42", "title": "Platform Engineer", "url": "/jobs/42"}
    data = {"props": {"pageProps": {"jobs": [item]}}}

    assert (
        _build_url(item, None, None, "https://careers.example") == "https://careers.example/jobs/42"
    )
    assert _build_url(item, "https://careers.example/jobs/{slug}", ["title"]) == (
        "https://careers.example/jobs/platform-engineer"
    )
    assert _find_jobs_path(data) == ("props.pageProps.jobs", 1)


def test_nextdata_prefers_vacancy_array_over_cms_job_configurator() -> None:
    job = {
        "name": "Platform Engineer",
        "unique_id": "job-42",
        "posting_type": "Standard",
        "city": [{"name": "Remote"}],
        "description": "Build reliable data platforms.",
        "seo": {"url": "/en/vacancy/platform-engineer-job-42"},
    }
    cms_config = {
        "title": "Job search config",
        "uid": "cms-1",
        "_content_type_uid": "job_list_search_configurator",
        "locale": "en-us",
        "description": "Page configuration.",
    }
    data = {
        "props": {
            "pageProps": {
                "cmsPageData": {"job_list_search_configurator": [cms_config]},
                "initialJobs": {"jobs": [job, {**job, "unique_id": "job-43"}]},
            }
        }
    }

    assert _find_jobs_path(data) == ("props.pageProps.initialJobs.jobs", 2)
    assert (
        _build_url(job, None, None, "https://careers.example/jobs")
        == "https://careers.example/en/vacancy/platform-engineer-job-42"
    )


def test_dom_detects_explicit_empty_board_message() -> None:
    assert _has_confirmed_empty_board_message(
        "<main><h1>Vacancies</h1><p>No vacancies for now. Please send your CV.</p></main>"
    )
    assert not _has_confirmed_empty_board_message(
        "<main><p>We have open positions in engineering and design.</p></main>"
    )


def test_dom_detects_explicitly_unfinished_career_board() -> None:
    assert _has_board_gone_message(
        "<main><h1>Careers</h1><p>Something interesting is about to be here. "
        "Please get back soon. Careers page under construction.</p></main>"
    )
    assert _has_board_gone_message(
        '<main class="under-construction"><p>Please get back soon.</p></main>'
    )
    assert _has_board_gone_message(
        "<h1>Platforma nu este disponibilă</h1>"
        "<p>toate anunțurile le puteți găsi pe altă platformă</p>"
    )
    assert not _has_board_gone_message("<main><p>Our careers page is live.</p></main>")


def test_soft_404_detects_h2_error_page() -> None:
    assert is_soft_404("<title>Company</title><h2>Oops, 404 Error!</h2>")


def test_board_gone_detects_unavailable_listing_platform() -> None:
    assert is_board_gone(
        "Platforma nu este disponibilă. Toate anunțurile le puteți găsi pe altă platformă."
    )


@pytest.mark.asyncio
async def test_nextdata_monitor_reads_recorded_listing() -> None:
    client = _Client(_listing_html())
    spec = SimpleNamespace(url="https://careers.example", monitor_config={})

    items = await discover(spec, client)

    assert len(items) == 1
    assert items[0].url == "https://careers.example/jobs/next-1"
    assert items[0].title == "Data Platform Engineer"
    assert items[0].locations == ["Remote"]


@pytest.mark.asyncio
async def test_nextdata_monitor_can_handle_recorded_listing() -> None:
    result = await can_handle("https://careers.example", _Client(_listing_html()))

    assert result == {"path": "props.pageProps.jobs", "count": 1}


@pytest.mark.asyncio
async def test_nextdata_monitor_supports_explicit_rich_field_mapping() -> None:
    spec = SimpleNamespace(
        url="https://careers.example",
        monitor_config={
            "path": "props.pageProps.jobs",
            "url_template": "https://careers.example/jobs/{id}-{slug}",
            "slug_fields": ["title"],
            "fields": {"title": "title", "locations": "location", "description": "description"},
        },
    )

    items = await discover(spec, _Client(_listing_html()))

    assert len(items) == 1
    assert items[0].url == "https://careers.example/jobs/next-1-data-platform-engineer"
    assert items[0].title == "Data Platform Engineer"
    assert items[0].locations == "Remote"


@pytest.mark.asyncio
async def test_nextdata_monitor_supports_url_only_mapping() -> None:
    spec = SimpleNamespace(
        url="https://careers.example",
        monitor_config={
            "path": "props.pageProps.jobs",
            "url_template": "https://careers.example/jobs/{id}",
        },
    )

    urls = await discover(spec, _Client(_listing_html()))

    assert urls == {"https://careers.example/jobs/next-1"}
