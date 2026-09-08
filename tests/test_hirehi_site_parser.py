from __future__ import annotations

from types import SimpleNamespace

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.hirehi import HireHiParser


class _Client:
    async def get(self, url: str, **_: object) -> SimpleNamespace:
        if "/development/" in url:
            return SimpleNamespace(
                url=url,
                text=(
                    '<script type="application/ld+json">'
                    '{"@type":"JobPosting","title":"AI Engineer",'
                    '"description":"<p>Build production AI systems.</p>"}'
                    "</script>"
                ),
                raise_for_status=lambda: None,
            )
        return SimpleNamespace(
            url=url,
            text=(
                '<script type="application/ld+json">'
                '{"@type":"ItemList","itemListElement":['
                '{"@type":"ListItem","item":{"url":"/development/ai-engineer-in-banking-87396",'
                '"name":"AI Engineer"}}]}'
                "</script>"
            ),
            raise_for_status=lambda: None,
        )


@pytest.mark.asyncio
async def test_hirehi_parser_reads_current_json_ld_listing() -> None:
    spec = CareerSiteSpec(
        url="https://hirehi.ru/?search=AI",
        source_name="hirehi_ru",
        limit=10,
    )

    items = [item async for item in HireHiParser().parse(spec, _Client())]

    assert len(items) == 1
    assert str(items[0].url) == "https://hirehi.ru/development/ai-engineer-in-banking-87396"
    assert "Build production AI systems." in items[0].text
