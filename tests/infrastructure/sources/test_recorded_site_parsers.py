from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.breezy import BreezyParser
from job_ftch.infrastructure.sources.site_parsers.btsdigital import BtsdigitalParser
from job_ftch.infrastructure.sources.site_parsers.euremotejobs import EuremotejobsParser
from job_ftch.infrastructure.sources.site_parsers.google import GoogleParser
from job_ftch.infrastructure.sources.site_parsers.microsoft import MicrosoftParser
from job_ftch.infrastructure.sources.site_parsers.payme import PaymeParser
from job_ftch.infrastructure.sources.site_parsers.raiffeisen import RaiffeisenParser
from job_ftch.infrastructure.sources.site_parsers.relocateme import RelocateMeParser


def _fixture_path(*parts: str) -> Path:
    return Path(__file__).parents[3] / "fixtures" / "real_world" / Path(*parts)


class _Response:
    def __init__(
        self, *, text: str = "", payload: object | None = None, status_code: int = 200
    ) -> None:
        self.text = text
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class _AsyncClient:
    def __init__(self, responses: list[_Response]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def __aenter__(self) -> _AsyncClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback

    async def get(self, url: str, **kwargs: object) -> _Response:
        self.calls.append((url, kwargs))
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_google_parser_reads_recorded_listing() -> None:
    html = _fixture_path("site_parsers", "google", "listing.html").read_text(encoding="utf-8")
    client = _AsyncClient([_Response(text=html)])
    spec = CareerSiteSpec(url="https://www.google.com/about/careers/applications/jobs/results")

    items = [item async for item in GoogleParser().parse(spec, client)]

    assert [item.external_id for item in items] == ["123456789012"]
    assert items[0].metadata["title"] == "Software Engineer"
    assert "123456789012-software-engineer" in str(items[0].url)


@pytest.mark.asyncio
async def test_breezy_parser_uses_runtime_client_and_honours_limit() -> None:
    client = _AsyncClient(
        [
            _Response(
                payload=[
                    {"id": "1", "name": "Backend", "url": "https://acme.breezy.hr/p/1"},
                    {"id": "2", "name": "Frontend", "url": "https://acme.breezy.hr/p/2"},
                ]
            )
        ]
    )
    spec = CareerSiteSpec(url="https://acme.breezy.hr/", limit=1)

    items = [item async for item in BreezyParser().parse(spec, client)]

    assert [item.external_id for item in items] == ["1"]
    assert client.calls[0][0] == "https://acme.breezy.hr/json"


@pytest.mark.asyncio
async def test_bts_parser_normalizes_retired_english_career_alias() -> None:
    client = _AsyncClient([_Response(text="<main>No listings</main>")])
    spec = CareerSiteSpec(url="https://btsdigital.kz/en/career")

    items = [item async for item in BtsdigitalParser().parse(spec, client)]

    assert items == []
    assert BtsdigitalParser.confirmed_empty_on_empty is True
    assert client.calls[0][0] == "https://btsdigital.kz/ru/career"


@pytest.mark.asyncio
async def test_microsoft_parser_reads_recorded_listing() -> None:
    payload = json.loads(
        _fixture_path("site_parsers", "microsoft", "listing.json").read_text(encoding="utf-8")
    )
    client = _AsyncClient([_Response(payload=payload)])
    spec = CareerSiteSpec(url="https://careers.microsoft.com/us/en/search-results?location=Redmond")

    items = [item async for item in MicrosoftParser().parse(spec, client)]

    assert [item.external_id for item in items] == ["1847362"]
    assert items[0].metadata["location"] == "Redmond, Washington"
    assert client.calls[0][1]["params"]["location"] == "Redmond"


@pytest.mark.asyncio
async def test_payme_parser_reads_recorded_listing() -> None:
    payload = json.loads(
        _fixture_path("site_parsers", "payme", "listing.json").read_text(encoding="utf-8")
    )
    client = _AsyncClient([_Response(payload=payload)])
    spec = CareerSiteSpec(url="https://career.payme.uz", source_name="Payme")

    items = [item async for item in PaymeParser().parse(spec, client)]

    assert [item.external_id for item in items] == ["payme-42"]
    assert items[0].metadata["title"] == "Backend Engineer"
    assert str(items[0].url).endswith("/vacancies/payme-42")


@pytest.mark.asyncio
async def test_relocateme_parser_reads_recorded_listing() -> None:
    html = _fixture_path("site_parsers", "relocateme", "listing.html").read_text(encoding="utf-8")
    client = _AsyncClient([_Response(text=html)])
    spec = CareerSiteSpec(url="https://relocate.me/jobs", source_name="RelocateMe")

    items = [item async for item in RelocateMeParser().parse(spec, client)]

    assert [item.external_id for item in items] == ["https://relocate.me/jobs/platform-engineer"]
    assert items[0].metadata["company"] == "Example Labs"
    assert items[0].metadata["location"] == "Berlin, Germany"


@pytest.mark.asyncio
async def test_euremotejobs_parser_reads_recorded_api_listing() -> None:
    payload = json.loads(
        _fixture_path("site_parsers", "euremotejobs", "listing.json").read_text(encoding="utf-8")
    )
    client = _AsyncClient([_Response(payload=payload)])
    spec = CareerSiteSpec(url="https://euremotejobs.com/jobs", source_name="EU Remote")

    items = [item async for item in EuremotejobsParser().parse(spec, client)]

    assert [item.external_id for item in items] == ["501"]
    assert items[0].metadata["location"] == "Remote, Europe"
    assert "Build reliable data systems" in items[0].text


@pytest.mark.asyncio
async def test_raiffeisen_parser_reads_recorded_api_listing() -> None:
    payload = json.loads(
        _fixture_path("site_parsers", "raiffeisen", "listing.json").read_text(encoding="utf-8")
    )
    client = _AsyncClient([_Response(payload=payload)])
    spec = CareerSiteSpec(url="https://career.raiffeisen.ru/vacancies", source_name="Raiffeisen")

    items = [item async for item in RaiffeisenParser().parse(spec, client)]

    assert [item.external_id for item in items] == ["raif-77"]
    assert items[0].metadata["location"] == "Moscow"
    assert "banking platform" in items[0].text
