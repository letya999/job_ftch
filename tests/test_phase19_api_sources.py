from unittest.mock import AsyncMock, MagicMock

import pytest

from job_ftch.domain import SourceKind
from job_ftch.domain.source_spec import RestAPISourceSpec
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
