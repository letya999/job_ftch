from __future__ import annotations

from dataclasses import dataclass

import pytest

from job_ftch.domain import SourceKind
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.justjoin import JustjoinItParser


@dataclass
class _FakeResponse:
    payload: dict[str, object]
    status_code: int = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self) -> dict[str, object]:
        return self.payload


class _FakeClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        self.calls.append((url, headers))
        return _FakeResponse(self.payload)


@pytest.mark.asyncio
async def test_justjoin_parser_emits_unique_raw_items() -> None:
    client = _FakeClient(
        {
            "data": [
                {
                    "slug": "senior-python-engineer",
                    "title": "Senior Python Engineer",
                    "publishedAt": "2026-07-05T10:00:00Z",
                    "multilocation": [{"city": "Warsaw"}, {"city": "Remote"}],
                    "employmentTypes": [{"type": "b2b"}],
                },
                {
                    "slug": "senior-python-engineer",
                    "title": "Duplicate",
                },
            ]
        }
    )
    spec = CareerSiteSpec(
        url="https://justjoin.it/job-offers/all-locations/python",
        source_name="justjoin",
        limit=10,
    )

    items = [item async for item in JustjoinItParser().parse(spec, client)]

    assert len(items) == 1
    assert items[0].source_kind is SourceKind.CAREER_SITE
    assert str(items[0].url) == "https://justjoin.it/job-offer/senior-python-engineer"
    assert items[0].metadata["locations"] == ["Warsaw", "Remote"]
    assert items[0].metadata["employment_type"] == "b2b"
    assert client.calls[0][0] == "https://api.justjoin.it/v2/user-panel/offers"
