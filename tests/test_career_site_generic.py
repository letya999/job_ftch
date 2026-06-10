from __future__ import annotations

import re
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.career_site import client_for_config
from job_ftch.infrastructure.sources.career_site_source import CareerSiteSource
from job_ftch.infrastructure.sources.monitors.api_sniffer import (
    _collect_payloads,
)
from job_ftch.infrastructure.sources.monitors.api_sniffer import (
    can_handle as api_sniffer_can_handle,
)
from job_ftch.infrastructure.sources.monitors.dom import (
    _extract_regex_urls,
)
from job_ftch.infrastructure.sources.monitors.dom import (
    discover as dom_discover,
)
from job_ftch.infrastructure.sources.nextdata_utils import extract_inertia_data, extract_nuxt_data
from job_ftch.infrastructure.sources.scrapers.dom import can_handle as dom_scraper_can_handle
from job_ftch.infrastructure.sources.scrapers.dom import scrape as dom_scrape
from job_ftch.infrastructure.sources.scrapers.embedded import parse_html
from job_ftch.infrastructure.sources.scrapers.json_ld import scrape as jsonld_scrape


@dataclass
class FakeResponse:
    text: str
    status_code: int = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class FakeHttpClient:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self._responses = responses

    async def get(self, url: str, *, follow_redirects: bool = True):  # type: ignore[no-untyped-def]
        del follow_redirects
        return self._responses[url]


class _FakeManagedClient:
    def __init__(self, marker: str) -> None:
        self.marker = marker

    async def __aenter__(self) -> _FakeManagedClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        return None


class _NoFetchClient:
    async def get(self, url: str, *, follow_redirects: bool = True):  # type: ignore[no-untyped-def]
        del url, follow_redirects
        raise AssertionError("network fetch should not happen when prefetched_html is provided")


def test_extract_nuxt_and_inertia_data():
    nuxt_html = """
    <script>
      window.__NUXT__={"job":{"title":"ML Engineer","description":"Build models"}}
    </script>
    """
    inertia_html = """
    <div id="app" data-page="{&quot;component&quot;:&quot;Vacancy&quot;,&quot;props&quot;:{&quot;vacancyList&quot;:{&quot;data&quot;:[{&quot;title&quot;:&quot;DS&quot;,&quot;viewUrl&quot;:&quot;https://example.com/job/1&quot;}]}}}"></div>
    """

    assert extract_nuxt_data(nuxt_html) == {
        "job": {"title": "ML Engineer", "description": "Build models"}
    }
    assert (
        extract_inertia_data(inertia_html)["props"]["vacancyList"]["data"][0]["viewUrl"]
        == "https://example.com/job/1"
    )


def test_embedded_parse_html_auto_detects_nuxt_job_object():
    html = """
    <script>
      window.__NUXT__={
        "payload":{
          "vacancy":{
            "title":"Senior ML Engineer",
            "description":"Own ranking models",
            "location":"Remote"
          }
        }
      }
    </script>
    """

    payload = parse_html(html, {})

    assert payload is not None
    assert payload.title == "Senior ML Engineer"
    assert payload.description == "Own ranking models"
    assert payload.locations == ["Remote"]


@pytest.mark.asyncio
async def test_dom_discover_expands_listing_pages():
    spec = SimpleNamespace(
        url="https://example.com/jobs/data-scientist",
        monitor_config={
            "expand_links": r"example\.com/jobs/data-scientist/[A-Za-z]+",
            "include_if_detail_page": False,
            "url_filter": r"example\.com/jobdesc\?id=\d+",
        },
    )
    client = FakeHttpClient(
        {
            "https://example.com/jobs/data-scientist": FakeResponse(
                '<a href="/jobs/data-scientist/Almaty">Almaty</a>'
            ),
            "https://example.com/jobs/data-scientist/Almaty": FakeResponse(
                '<a href="/jobdesc?id=42">ML Engineer</a>'
            ),
        }
    )

    urls = await dom_discover(spec, client)

    assert urls == {"https://example.com/jobdesc?id=42"}


def test_extract_regex_urls_preserves_host_like_paths():
    html = 'href="hh.ru/vacancy/123" href="career.wb.ru/vacancies/42"'

    urls = _extract_regex_urls(
        html,
        "https://gderabota.ru/vacancies/data-scientist",
        re.compile(r"(hh\.ru/vacancy/\d+|career\.wb\.ru/vacancies/\d+)", re.IGNORECASE),
    )

    assert urls == {
        "https://hh.ru/vacancy/123",
        "https://career.wb.ru/vacancies/42",
    }


@pytest.mark.asyncio
async def test_career_site_source_scrapes_detail_page_with_inferred_dom_steps():
    spec = CareerSiteSpec(
        type="career_site",
        url="https://example.com/vacancies/senior-ml-engineer",
        monitor="dom",
        monitor_config={"include_self_url": True},
        limit=5,
        source_name="example_detail",
    )
    html = """
    <html>
      <body>
        <h1>Senior ML Engineer</h1>
        <div>Lead applied ML systems for search and ranking.</div>
      </body>
    </html>
    """
    client = FakeHttpClient(
        {
            "https://example.com/vacancies/senior-ml-engineer": FakeResponse(html),
        }
    )
    source = CareerSiteSource(spec=spec, http_client=client, auth=MagicMock())

    items = [item async for item in source.fetch()]

    assert len(items) == 1
    assert items[0].source_name == "example_detail"
    assert "Senior ML Engineer" in items[0].text


def test_dom_scraper_can_handle_title_only_detail_pages():
    html = """
    <html>
      <head><title>Risk Data Scientist | T-Bank</title></head>
      <body>
        <p>Risk Data Scientist</p>
        <p>Build scoring models and analyze large data sets.</p>
      </body>
    </html>
    """

    config = dom_scraper_can_handle([html])

    assert config is not None
    assert config["steps"][0]["tag"] == "title"


@pytest.mark.asyncio
async def test_api_sniffer_can_handle_html_with_api_hints():
    client = FakeHttpClient(
        {
            "https://example.com/careers": FakeResponse(
                '<script>fetch("/api/vacancies/search")</script>'
            )
        }
    )

    result = await api_sniffer_can_handle("https://example.com/careers", client)

    assert result == {"browser": True}


def test_api_sniffer_collects_rich_payloads_without_explicit_urls():
    data = {
        "data": {
            "vacancy_item_list": {
                "List": [
                    {
                        "vacancy_id": "97",
                        "title": "Python Backend Engineer",
                        "short_description": "Build backend services",
                        "employment_type_label": "Full time",
                    }
                ]
            }
        }
    }

    payloads = _collect_payloads(data, "https://people.beeline.kz")

    assert set(payloads) == {"https://people.beeline.kz/#job-97"}
    assert payloads["https://people.beeline.kz/#job-97"].title == "Python Backend Engineer"


@pytest.mark.asyncio
async def test_client_for_config_reuses_outer_client_without_skip_ssl():
    outer = object()

    async with client_for_config(outer, {}) as selected:
        assert selected is outer


@pytest.mark.asyncio
async def test_client_for_config_builds_scoped_insecure_client(monkeypatch: pytest.MonkeyPatch):
    built: list[bool] = []

    def fake_build_default_http_client(*, verify_ssl: bool = True) -> _FakeManagedClient:
        built.append(verify_ssl)
        return _FakeManagedClient("insecure")

    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.career_site.build_default_http_client",
        fake_build_default_http_client,
    )

    async with client_for_config(object(), {"skip_ssl": True}) as selected:
        assert isinstance(selected, _FakeManagedClient)
        assert selected.marker == "insecure"

    assert built == [False]


@pytest.mark.asyncio
async def test_dom_scrape_uses_prefetched_html_without_network():
    payload = await dom_scrape(
        "https://example.com/job",
        {
            "steps": [
                {"tag": "title", "field": "title"},
                {
                    "tag": "title",
                    "offset": 1,
                    "field": "description",
                    "html": True,
                    "optional": True,
                    "stop_count": 1,
                },
            ],
            "prefetched_html": "<html><head><title>ML Engineer | Example</title></head><body><p>Build models</p></body></html>",
        },
        _NoFetchClient(),
    )

    assert payload is not None
    assert payload.title == "ML Engineer"


@pytest.mark.asyncio
async def test_jsonld_scrape_uses_prefetched_html_without_network():
    payload = await jsonld_scrape(
        "https://example.com/job",
        {
            "prefetched_html": (
                '<script type="application/ld+json">'
                '{"@type":"JobPosting","title":"DS","description":"Train models"}'
                "</script>"
            )
        },
        _NoFetchClient(),
    )

    assert payload is not None
    assert payload.title == "DS"
    assert payload.description == "Train models"
