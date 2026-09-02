from __future__ import annotations

import pytest

from job_ftch.application.registry import resolve_site_parser
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.enbek import EnbekParser
from job_ftch.infrastructure.sources.site_parsers.higgsfield import HiggsfieldParser
from job_ftch.infrastructure.sources.site_parsers.kcell import KcellParser
from job_ftch.infrastructure.sources.site_parsers.protected_defaults import (
    ProtectedBrowserDefaultsParser,
)


class _Response:
    def __init__(self, *, text: str = "", payload: object | None = None, url: str = "") -> None:
        self.text = text
        self.url = url
        self.status_code = 200
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class _Client:
    def __init__(self, responses: dict[str, _Response]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    async def get(self, url: str, **_: object) -> _Response:
        self.calls.append(url)
        for prefix, response in self._responses.items():
            if url == prefix or url.startswith(prefix):
                if not response.url:
                    response.url = url
                return response
        raise AssertionError(url)


def test_higgsfield_parser_wins_over_protected_defaults() -> None:
    parser = resolve_site_parser("https://careers.higgsfield.kz/")
    assert isinstance(parser, HiggsfieldParser)
    assert not isinstance(parser, ProtectedBrowserDefaultsParser)


@pytest.mark.asyncio
async def test_kcell_parser_emits_public_api_jobs() -> None:
    payload = {
        "content": [
            {
                "jobId": 359,
                "nameRu": "Data Engineer",
                "descRu": "Build the lake.",
                "city": {"nameRu": "Алматы"},
                "team": {"nameRu": "DWH"},
                "createdDate": "2026-09-01T10:00:00Z",
            }
        ],
        "last": True,
    }
    items = [
        item
        async for item in KcellParser().parse(
            CareerSiteSpec(url="https://jobs.kcell.kz/", source_name="kcell", limit=5),
            _Client({"https://jobs.kcell.kz/api/jobs": _Response(payload=payload)}),
        )
    ]
    assert len(items) == 1
    assert items[0].external_id == "359"
    assert str(items[0].url) == "https://jobs.kcell.kz/job/359"
    assert "Build the lake." in items[0].text
    assert items[0].metadata["parser"] == "kcell_api"


def test_enbek_parser_emits_stable_vacancy_links() -> None:
    html = """
    <a href="/ru/vacancy/data-engineer~5916504" title="Data Engineer">Data Engineer</a>
    <div>ТОО Example, г.Алматы, от 500 000 тг.</div>
    """
    spec = CareerSiteSpec(url="https://www.enbek.kz/ru/search/vacancy", source_name="enbek")
    items = EnbekParser._items_from_html(html, spec.url, spec)
    assert len(items) == 1
    assert items[0].external_id == "5916504"
    assert str(items[0].url).endswith("/ru/vacancy/data-engineer~5916504")


@pytest.mark.asyncio
async def test_higgsfield_parser_emits_ashby_api_payloads() -> None:
    listing = {
        "jobs": [
            {
                "id": "ashby-1",
                "title": "Growth Product Manager",
                "jobUrl": "https://jobs.ashbyhq.com/higgsfieldai/ashby-1",
                "descriptionPlain": "Own PLG.",
                "isListed": True,
                "department": "Product",
            }
        ]
    }

    class _AshbyClient:
        async def get(self, url: str, **_: object) -> _Response:
            if "careers.higgsfield.kz" in url:
                response = _Response(text="<html>redirect</html>", url="https://jobs.ashbyhq.com/higgsfieldai")
                return response
            return _Response(payload=listing, url=url)

    items = [
        item
        async for item in HiggsfieldParser().parse(
            CareerSiteSpec(url="https://careers.higgsfield.kz/", source_name="higgsfield", limit=5),
            _AshbyClient(),
        )
    ]
    assert len(items) == 1
    assert "Growth Product Manager" in items[0].text
    assert items[0].metadata["parser"] == "higgsfield_ashby"
    assert items[0].metadata["company"] == "Higgsfield"
