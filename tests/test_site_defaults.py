from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from job_ftch.application.source_inputs import build_source_spec_from_input
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.career_site_source import CareerSiteSource


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class FakeHttpClient:
    def __init__(self, responses: dict[str, str]) -> None:
        self._responses = responses

    async def get(self, url: str, *, follow_redirects: bool = True) -> FakeResponse:
        del follow_redirects
        return FakeResponse(self._responses[url])


@pytest.mark.asyncio
async def test_build_source_spec_from_hh_root_applies_runtime_defaults() -> None:
    spec = await build_source_spec_from_input("https://hh.ru/")

    assert spec.type == "career_site"
    assert spec.monitor_config["url_filter"] == r"hh\.(?:ru|kz)/vacancy/\d+"
    assert spec.url_filter == r"hh\.(?:ru|kz)/vacancy/\d+"


@pytest.mark.asyncio
async def test_build_source_spec_from_tbank_root_applies_runtime_defaults() -> None:
    spec = await build_source_spec_from_input("https://www.tbank.ru/career/")

    assert spec.type == "career_site"
    assert spec.monitor_config["url_filter"] == r"tbank\.ru/career/it/vacancy/[a-z0-9\-/]+"
    assert spec.monitor_config["expand_links"] == [
        r"tbank\.ru/career/it(?:/|$)",
        r"tbank\.ru/career/it/ml(?:/|$)",
        r"tbank\.ru/career/vacancies/it(?:/|\?|$)",
    ]
    assert spec.url_filter == r"tbank\.ru/career/it/vacancy/[a-z0-9\-/]+"


@pytest.mark.asyncio
async def test_career_site_source_uses_tbank_root_defaults_for_one_level_expansion() -> None:
    spec = CareerSiteSpec(
        type="career_site",
        url="https://www.tbank.ru/career/",
        monitor="dom",
        limit=5,
        source_name="tbank_root",
    )
    client = FakeHttpClient(
        {
            "https://www.tbank.ru/career/": (
                '<a href="/career/it/">IT jobs</a>'
                '<a href="/career/blog/">Blog</a>'
            ),
            "https://www.tbank.ru/career/it/": (
                '<a href="/career/it/vacancy/ml-engineer/">ML Engineer</a>'
            ),
            "https://www.tbank.ru/career/it/vacancy/ml-engineer/": (
                "<html><body><h1>ML Engineer</h1><p>Build ranking systems</p></body></html>"
            ),
        }
    )
    source = CareerSiteSource(spec=spec, http_client=client, auth=MagicMock())

    items = [item async for item in source.fetch()]

    assert len(items) == 1
    assert str(items[0].url) == "https://www.tbank.ru/career/it/vacancy/ml-engineer/"
    assert "ML Engineer" in items[0].text
