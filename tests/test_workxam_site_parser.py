from __future__ import annotations

import html
import json

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.workxam import WorkxAmParser


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        encoded = html.escape(json.dumps(payload), quote=True)
        self.text = f'<div id="app" data-page="{encoded}"></div>'

    def raise_for_status(self) -> None:
        pass


class _Client:
    async def get(self, _url: str) -> _Response:
        return _Response(
            {
                "props": {
                    "jobs": [
                        "unexpected-row",
                        {"slug": "ai-engineer", "title": "AI Engineer", "company": "Hidden"},
                    ]
                }
            }
        )


@pytest.mark.asyncio
async def test_workxam_ignores_non_object_rows() -> None:
    spec = CareerSiteSpec(url="https://workx.am/", source_name="workx", limit=1)
    items = [item async for item in WorkxAmParser().parse(spec, _Client())]
    assert [str(item.url) for item in items] == ["https://workx.am/jobs/ai-engineer"]
