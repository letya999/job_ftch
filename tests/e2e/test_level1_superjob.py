from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
import pytest
import yaml

from job_ftch.domain.source_spec import RestAPISourceSpec
from job_ftch.infrastructure.sources.api.base import OfficialAPISource


@pytest.fixture
def superjob_spec() -> RestAPISourceSpec:
    path = Path(__file__).parent.parent.parent / "fixtures" / "specs" / "superjob_ml.yaml"
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return RestAPISourceSpec(**data)


@pytest.mark.anyio
async def test_superjob_fixture_field_mapping(
    superjob_spec: RestAPISourceSpec, superjob_json: dict, monkeypatch, null_auth
) -> None:
    # Mock httpx response
    class MockResponse:
        def __init__(self, json_data: dict):
            self._json = json_data
            self.status_code = 200

        def json(self):
            return self._json

        def raise_for_status(self):
            pass

    async def mock_get(*args, **kwargs):
        return MockResponse(superjob_json)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    # Use NullAuth because we mock the response
    source = OfficialAPISource(spec=superjob_spec, auth=null_auth)

    items = []
    async for item in source.fetch():
        items.append(item)

    assert len(items) == 3
    # Check field mapping from superjob_ml.yaml
    # profession -> title
    # link -> url
    # firm_name -> company
    # town.title -> location
    # candidat -> description

    item = items[0]
    fixture_item = superjob_json["objects"][0]

    assert fixture_item["profession"] in item.text
    assert str(item.url) == fixture_item["link"]
    assert fixture_item["firm_name"] in item.text
    assert fixture_item["town"]["title"] in item.text
    assert fixture_item["candidat"] in item.text
    assert item.external_id == str(fixture_item["id"])


@pytest.mark.network
@pytest.mark.superjob
@pytest.mark.anyio
async def test_superjob_live_fetch(superjob_spec: RestAPISourceSpec) -> None:
    api_key = os.environ.get("SUPERJOB_API_KEY")
    if not api_key:
        pytest.skip("SUPERJOB_API_KEY not set")

    from unittest.mock import MagicMock

    mock_auth = MagicMock()
    mock_auth.resolve.return_value = {"X-Api-App-Id": api_key}
    source = OfficialAPISource(spec=superjob_spec, auth=mock_auth)

    items = []
    async with asyncio.timeout(15):
        async for item in source.fetch():
            items.append(item)
            if len(items) >= 20:
                break

    assert len(items) >= 1
    assert all(item.text for item in items)
    assert all(item.url for item in items)
