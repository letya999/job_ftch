from __future__ import annotations

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.gorodrabot import GorodRabotParser


class _Response:
    status_code = 200
    url = "https://belarus.gorodrabot.by/"
    text = """
        <a href="/advert/12345/senior-data-engineer">Vacancy</a>
        <a href="https://direct.yandex.ru/?partner=ad">Advertisement</a>
        <a href="/companies/acme">Company</a>
    """

    def raise_for_status(self) -> None:
        return None


class _Client:
    async def get(self, url: str, *, follow_redirects: bool = True) -> _Response:
        assert url == "https://belarus.gorodrabot.by/"
        assert follow_redirects is True
        return _Response()


@pytest.mark.asyncio
async def test_gorodrabot_discover_returns_only_vacancy_details() -> None:
    urls = await GorodRabotParser().discover(
        CareerSiteSpec(url="https://belarus.gorodrabot.by/", limit=5), _Client()
    )

    assert urls == ["https://belarus.gorodrabot.by/advert/12345/senior-data-engineer"]
