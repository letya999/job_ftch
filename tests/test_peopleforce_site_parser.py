from __future__ import annotations

from types import SimpleNamespace

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.peopleforce import PeopleForceCareerParser


@pytest.mark.asyncio
async def test_peopleforce_discovers_canonical_vacancy_urls() -> None:
    parser = PeopleForceCareerParser()

    class _Response(SimpleNamespace):
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    class _Client:
        async def get(self, url: str, *, follow_redirects: bool = True) -> _Response:
            del follow_redirects
            return _Response(
                text="""
                <a href="/careers/v/2832-deputy-head-of-international-legal-department">Role</a>
                <a href="/careers/v/2832-deputy-head-of-international-legal-department?ref=listing#description">Duplicate</a>
                <a href="https://untrusted.example/careers/v/9999-not-a-vacancy">External</a>
                <a href="/careers?page=2">Next page</a>
                """,
                url=url,
            )

    spec = CareerSiteSpec(
        url="https://peopleforce.softconstruct.com/careers",
        source_name="peopleforce",
    )

    assert await parser.discover(spec, _Client()) == [
        "https://peopleforce.softconstruct.com/careers/v/2832-deputy-head-of-international-legal-department"
    ]


@pytest.mark.asyncio
async def test_peopleforce_keeps_redirected_detail_page() -> None:
    parser = PeopleForceCareerParser()

    class _Response(SimpleNamespace):
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    class _Client:
        async def get(self, url: str, *, follow_redirects: bool = True) -> _Response:
            del url, follow_redirects
            return _Response(
                text="unused",
                url="https://peopleforce.softconstruct.com/careers/v/2832-deputy-head-of-international-legal-department?ref=campaign",
            )

    spec = CareerSiteSpec(
        url="https://peopleforce.softconstruct.com/careers/v/2832-deputy-head-of-international-legal-department",
        source_name="peopleforce",
    )

    assert await parser.discover(spec, _Client()) == [
        "https://peopleforce.softconstruct.com/careers/v/2832-deputy-head-of-international-legal-department"
    ]
