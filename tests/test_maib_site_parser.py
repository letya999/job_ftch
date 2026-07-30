from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.maib import MaibParser


@pytest.mark.asyncio
async def test_discovers_only_maib_career_detail_pages() -> None:
    client = AsyncMock()
    client.get.return_value = httpx.Response(
        200,
        text="""
            <a href=\"/ro/maib/cariera\">Career</a>
            <a href=\"/ro/maib/cariera/expert-contabilitate-160726\">Role</a>
            <a href=\"/ro/maib/cariera/expert-contabilitate-160726?source=home\">Duplicate</a>
        """,
        request=httpx.Request("GET", "https://www.maib.md/ro/maib/cariera"),
    )
    spec = CareerSiteSpec(url="https://maib.md/ro/cariere", source_name="Maib")

    urls = await MaibParser().discover(spec, client)

    assert urls == ["https://www.maib.md/ro/maib/cariera/expert-contabilitate-160726"]
