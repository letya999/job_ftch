from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from job_ftch.application.source_inputs import build_source_spec_from_input
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.career_site_source import CareerSiteSource


class FakeResponse:
    def __init__(self, url: str, text: str) -> None:
        self.url = url
        self.text = text
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None


class FakeHttpClient:
    def __init__(self, responses: dict[str, str]) -> None:
        self._responses = responses

    async def get(self, url: str, *, follow_redirects: bool = True) -> FakeResponse:
        del follow_redirects
        return FakeResponse(url, self._responses[url])


@pytest.mark.asyncio
async def test_build_source_spec_from_hh_root_applies_runtime_defaults() -> None:
    from job_ftch.infrastructure.sources.site_defaults import apply_runtime_defaults

    spec = await build_source_spec_from_input(
        "https://hh.ru/", runtime_defaults_fn=apply_runtime_defaults
    )

    assert spec.type == "career_site"
    assert spec.monitor_config["url_filter"] == (
        r"(?:[a-z0-9-]+\.)?(?:hh\.(?:ru|kz|uz|by)|hh1\.az|headhunter\.kg|rabota\.by)/vacancy/\d+"
    )
    assert spec.url_filter == (
        r"(?:[a-z0-9-]+\.)?(?:hh\.(?:ru|kz|uz|by)|hh1\.az|headhunter\.kg|rabota\.by)/vacancy/\d+"
    )


@pytest.mark.asyncio
async def test_build_source_spec_from_tbank_root_applies_runtime_defaults() -> None:
    from job_ftch.infrastructure.sources.site_defaults import apply_runtime_defaults

    spec = await build_source_spec_from_input(
        "https://www.tbank.ru/career/", runtime_defaults_fn=apply_runtime_defaults
    )

    assert spec.type == "career_site"
    assert spec.monitor_config["url_filter"] == (
        r"tbank\.ru/career/(?:it/)?vacanc(?:y|ies)/(?:[a-z0-9_-]+/)+[a-z0-9_-]+/?$"
    )
    assert spec.monitor_config["expand_links"] == [
        r"tbank\.ru/career/it(?:/|$)",
        r"tbank\.ru/career/it/ml(?:/|$)",
        r"tbank\.ru/career/vacancies/it(?:/|\?|$)",
    ]
    assert spec.url_filter == (
        r"tbank\.ru/career/(?:it/)?vacanc(?:y|ies)/(?:[a-z0-9_-]+/)+[a-z0-9_-]+/?$"
    )


@pytest.mark.asyncio
async def test_build_source_spec_from_rabota_root_applies_runtime_defaults() -> None:
    from job_ftch.infrastructure.sources.site_defaults import apply_runtime_defaults

    spec = await build_source_spec_from_input(
        "https://rabota.by/", runtime_defaults_fn=apply_runtime_defaults
    )

    assert spec.type == "career_site"
    assert spec.monitor_config["url_filter"] == r"(?:[a-z0-9-]+\.)?rabota\.by/vacancy/\d+"
    assert spec.url_filter == r"(?:[a-z0-9-]+\.)?rabota\.by/vacancy/\d+"


@pytest.mark.asyncio
async def test_build_source_spec_from_djinni_root_applies_runtime_defaults() -> None:
    from job_ftch.infrastructure.sources.site_defaults import apply_runtime_defaults

    spec = await build_source_spec_from_input(
        "https://djinni.co/jobs/", runtime_defaults_fn=apply_runtime_defaults
    )

    assert spec.type == "career_site"
    assert spec.monitor_config["url_filter"] == r"djinni\.co/jobs/\d+-[a-z0-9-]+/?$"
    assert spec.url_filter == r"djinni\.co/jobs/\d+-[a-z0-9-]+/?$"


@pytest.mark.asyncio
async def test_build_source_spec_from_dou_jobs_root_applies_runtime_defaults() -> None:
    from job_ftch.infrastructure.sources.site_defaults import apply_runtime_defaults

    spec = await build_source_spec_from_input(
        "https://jobs.dou.ua/vacancies/", runtime_defaults_fn=apply_runtime_defaults
    )

    assert spec.type == "career_site"
    assert (
        spec.monitor_config["url_filter"] == r"jobs\.dou\.ua/companies/[a-z0-9-]+/vacancies/\d+/?$"
    )
    assert spec.url_filter == r"jobs\.dou\.ua/companies/[a-z0-9-]+/vacancies/\d+/?$"


@pytest.mark.asyncio
async def test_generic_source_defaults_exclude_geekjob_and_superjob_listing_pages() -> None:
    from job_ftch.infrastructure.sources.site_defaults import apply_runtime_defaults

    geekjob = apply_runtime_defaults(CareerSiteSpec(url="https://geekjob.ru/vacancies?query=AI"))
    superjob = apply_runtime_defaults(
        CareerSiteSpec(url="https://www.superjob.ru/vakansii/?keywords=AI")
    )

    assert geekjob.url_filter == r"geekjob\.ru/(?:vacancy/[a-z0-9-]+/?|jobs/\d+/?$)"
    assert superjob.url_filter == r"superjob\.ru/vakansii/[a-z0-9-]+-\d+\.html$"


def test_protected_parser_defaults_declare_only_authorized_domains() -> None:
    from job_ftch.infrastructure.sources.site_defaults import apply_runtime_defaults

    for url in (
        "https://jobs.ashbyhq.com/example",
        "https://job.beeline.ru/vacancies",
    ):
        config = apply_runtime_defaults(CareerSiteSpec(url=url)).monitor_config
        assert config["captcha_authorized_domains"] == [
            "jobs.ashbyhq.com",
            "job.beeline.ru",
        ]
        assert config["proxy_rescue_allow_domains"] == config["captcha_authorized_domains"]

    beeline = apply_runtime_defaults(CareerSiteSpec(url="https://job.beeline.ru/vacancies"))
    assert beeline.monitor_config["proxy_geo"] == "RU"

    higgsfield = apply_runtime_defaults(CareerSiteSpec(url="https://careers.higgsfield.kz/"))
    assert higgsfield.monitor_config["captcha_authorized_domains"] == [
        "careers.higgsfield.kz",
        "jobs.ashbyhq.com",
        "api.ashbyhq.com",
    ]
    assert higgsfield.monitor_config["proxy_rescue_allow_domains"] == [
        "careers.higgsfield.kz",
        "jobs.ashbyhq.com",
        "api.ashbyhq.com",
    ]

    superjob = apply_runtime_defaults(CareerSiteSpec(url="https://www.superjob.ru/vakansii/"))
    assert superjob.monitor_config["captcha_authorized_domains"] == [
        "www.superjob.ru",
        "superjob.ru",
    ]
    assert superjob.monitor_config["bypass_capability"] == "cloudflare_challenge"


@pytest.mark.asyncio
async def test_career_site_source_uses_tbank_root_defaults_for_one_level_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = CareerSiteSpec(
        type="career_site",
        url="https://www.tbank.ru/career/",
        monitor="dom",
        limit=1,
        source_name="tbank_root",
    )
    client = FakeHttpClient(
        {
            "https://www.tbank.ru/career/": (
                '<a href="/career/it/">IT jobs</a><a href="/career/blog/">Blog</a>'
            ),
            "https://www.tbank.ru/career/it/": (
                '<a href="/career/it/vacancy/moscow/ml-engineer/">ML Engineer</a>'
            ),
            "https://www.tbank.ru/career/it/vacancy/moscow/ml-engineer/": (
                "<html><body><h1>ML Engineer</h1><p>Build ranking systems</p></body></html>"
            ),
        }
    )
    source = CareerSiteSource(spec=spec, http_client=client, auth=MagicMock())

    async def _no_browser(_: str) -> None:
        return None

    monkeypatch.setattr(source, "_fetch_detail_html_with_browser", _no_browser)

    items = [item async for item in source.fetch()]

    assert len(items) == 1
    assert str(items[0].url) == ("https://www.tbank.ru/career/it/vacancy/moscow/ml-engineer/")
    assert "ML Engineer" in items[0].text
