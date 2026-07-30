from __future__ import annotations

from types import SimpleNamespace

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.kaspersky import (
    KasperskyCareerParser,
    _extract_detail_urls,
)


def test_extract_detail_urls_finds_vacancy_links() -> None:
    html = """
    <a href="/vacancies">Vacancies</a>
    <a href="/vacancy/25265">ML Engineer</a>
    """

    urls = _extract_detail_urls(
        html,
        "https://careers.kaspersky.ru/vacancies",
        limit=5,
        detail_re=KasperskyCareerParser()._detail_re(),
    )

    assert urls == ["https://careers.kaspersky.ru/vacancy/25265"]


@pytest.mark.asyncio
async def test_kaspersky_parser_discovers_listing_urls() -> None:
    parser = KasperskyCareerParser()

    class _Response(SimpleNamespace):
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    class _Client:
        async def get(self, url: str, *, follow_redirects: bool = True) -> _Response:
            del follow_redirects
            return _Response(
                text='<a href="/vacancy/25265">ML Engineer</a>',
                url=url,
            )

    spec = CareerSiteSpec(url="https://careers.kaspersky.ru/vacancies", source_name="kaspersky")
    urls = await parser.discover(spec, _Client())

    assert urls == ["https://careers.kaspersky.ru/vacancy/25265"]
