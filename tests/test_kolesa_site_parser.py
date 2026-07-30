from __future__ import annotations

from types import SimpleNamespace

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_defaults import apply_runtime_defaults
from job_ftch.infrastructure.sources.site_parsers.kolesa import (
    KolesaCareerParser,
    _extract_detail_urls,
)


def test_extract_detail_urls_finds_kolesa_job_links() -> None:
    html = """
    <a href="/career/job/middle-php-backend-razrabotcik-krishakz-133298616">Role</a>
    <a href="/career/job">Listing</a>
    <a href="/career/we">Culture</a>
    """

    urls = _extract_detail_urls(
        html,
        "https://kolesa.group/career/job",
        limit=5,
        detail_re=KolesaCareerParser()._detail_re(),
    )

    assert urls == [
        "https://kolesa.group/career/job/middle-php-backend-razrabotcik-krishakz-133298616"
    ]


def test_kolesa_runtime_defaults_match_search_listing_url() -> None:
    spec = apply_runtime_defaults(CareerSiteSpec(url="https://kolesa.group/career/job?search=AI"))

    assert spec.monitor_config["include_if_detail_page"] is True


@pytest.mark.asyncio
async def test_kolesa_parser_discovers_listing_urls() -> None:
    parser = KolesaCareerParser()

    class _Response(SimpleNamespace):
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    class _Client:
        async def get(self, url: str, *, follow_redirects: bool = True) -> _Response:
            del follow_redirects
            return _Response(
                text='<a href="/career/job/middle-php-backend-razrabotcik-krishakz-133298616">Role</a>',
                url=url,
            )

    spec = CareerSiteSpec(url="https://kolesa.group/career/job", source_name="kolesa")
    urls = await parser.discover(spec, _Client())

    assert urls == [
        "https://kolesa.group/career/job/middle-php-backend-razrabotcik-krishakz-133298616"
    ]
