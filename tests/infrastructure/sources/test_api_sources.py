from unittest.mock import AsyncMock, MagicMock

import pytest

from job_ftch.domain import SourceKind
from job_ftch.domain.source_spec import RestAPISourceSpec
from job_ftch.infrastructure.sources.api.base import OfficialAPISource
from job_ftch.infrastructure.sources.api.greenhouse import GreenhouseAPISource
from job_ftch.infrastructure.sources.api.hh import HHAPISource


@pytest.mark.asyncio
async def test_greenhouse_mapping():
    spec = RestAPISourceSpec(
        base_url="https://api.greenhouse.io/v1/boards/test/",
        jobs_endpoint="jobs",
    )
    auth = MagicMock()
    store = MagicMock()
    store.get_run_state = AsyncMock(return_value=None)

    adapter = GreenhouseAPISource(spec, auth, store)

    # Mock data from Greenhouse
    item = {
        "id": 123,
        "absolute_url": "https://test.com/job/123",
        "title": "Engineer",
        "content": "Description here",
        "location": {"name": "New York"},
    }

    raw = adapter._map_to_raw_item(item)

    assert raw.external_id == "123"
    assert str(raw.url) == "https://test.com/job/123"
    assert raw.text == "Description here"
    assert raw.source_kind == SourceKind.CAREER_SITE


@pytest.mark.asyncio
async def test_hh_mapping():
    spec = RestAPISourceSpec(
        base_url="https://api.hh.ru/",
        jobs_endpoint="vacancies",
    )
    auth = MagicMock()
    store = MagicMock()
    store.get_run_state = AsyncMock(return_value=None)

    adapter = HHAPISource(spec, auth, store)

    # Mock data from HH.ru
    item = {
        "id": "456",
        "alternate_url": "https://hh.ru/vacancy/456",
        "name": "Python Developer",
        "snippet": {"requirement": "Strong Python knowledge"},
        "employer": {"name": "Acme Corp"},
        "area": {"name": "Moscow"},
    }

    raw = adapter._map_to_raw_item(item)

    assert raw.external_id == "456"
    assert str(raw.url) == "https://hh.ru/vacancy/456"
    assert raw.text == "Strong Python knowledge"
    assert raw.metadata["employer"]["name"] == "Acme Corp"


@pytest.mark.asyncio
async def test_official_api_source_uses_incremental_cursor_storage():
    spec = RestAPISourceSpec(
        base_url="https://api.example.com/",
        jobs_endpoint="vacancies",
        incremental_cursor_field="since_id",
    )
    auth = MagicMock()
    store = MagicMock()
    store.get = AsyncMock(return_value="42")
    store.set = AsyncMock()

    class ExampleAPISource(OfficialAPISource):
        pass

    source = ExampleAPISource(spec, auth, store)

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = [{"id": 99, "title": "Role", "description": "Desc"}]

    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=response)

    from unittest.mock import patch

    with patch("httpx.AsyncClient", return_value=client):
        items = [item async for item in source.fetch()]

    assert len(items) == 1
    expected_key = f"{source.source_kind}:{source.source_name}:cursor"
    store.get.assert_awaited_once_with(expected_key)
    client.get.assert_awaited_once()
    assert client.get.await_args.kwargs["params"]["since_id"] == "42"
    store.set.assert_awaited_once_with(expected_key, "99")


@pytest.mark.asyncio
async def test_official_api_source_reads_items_envelope():
    spec = RestAPISourceSpec(
        base_url="https://api.example.com/",
        jobs_endpoint="vacancies",
        field_map={
            "external_id": "id",
            "url": "alternate_url",
            "text": "snippet.requirement",
        },
    )
    auth = MagicMock()
    source = OfficialAPISource(spec, auth, None)

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "items": [
            {
                "id": "456",
                "alternate_url": "https://example.com/vacancy/456",
                "snippet": {"requirement": "Python"},
            }
        ]
    }

    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=response)

    from unittest.mock import patch

    with patch("httpx.AsyncClient", return_value=client):
        items = [item async for item in source.fetch()]

    assert len(items) == 1
    assert items[0].external_id == "456"
    assert str(items[0].url) == "https://example.com/vacancy/456"
