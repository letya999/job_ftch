from __future__ import annotations

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.large_employer_boards import (
    AlfaBankParser,
    CasibCareerParser,
    KonturCareerParser,
    TbcUzbekistanCareerParser,
    UzumCareerParser,
    VtbParser,
    YadroParser,
    YandexUzbekistanParser,
)


class _Response:
    text = """
    <main>
      <a href="/vacancy/102472">Senior Full-Stack Developer (AI Telecom)</a>
      <a href="/vacancy/102472">duplicate</a>
      <a href="/about">About</a>
    </main>
    """

    def raise_for_status(self) -> None:
        return None


class _Client:
    async def get(self, url: str, **_: object) -> _Response:
        del url
        return _Response()


@pytest.mark.asyncio
async def test_yadro_parser_emits_unique_detail_cards() -> None:
    items = [
        item
        async for item in YadroParser().parse(
            CareerSiteSpec(url="https://careers.yadro.com/vacancies", limit=5),
            _Client(),
        )
    ]
    assert len(items) == 1
    assert items[0].external_id == "102472"
    assert items[0].metadata["parser"] == "yadro_board"


@pytest.mark.asyncio
async def test_large_employer_parser_discovers_details_for_core_enrichment() -> None:
    urls = await YadroParser().discover(
        CareerSiteSpec(url="https://careers.yadro.com/vacancies", limit=5),
        _Client(),
    )

    assert urls == ["https://careers.yadro.com/vacancy/102472"]


def test_vtb_runtime_defaults_relax_tls_and_admit_proxy_host() -> None:
    from job_ftch.infrastructure.sources.site_defaults import apply_runtime_defaults

    spec = apply_runtime_defaults(CareerSiteSpec(url="https://rabota.vtb.ru/career-it/"))
    assert spec.monitor_config["skip_ssl"] is True
    assert spec.monitor_config["proxy_rescue_allow_domains"] == [
        "rabota.vtb.ru",
        "rabota-vtb.ru",
    ]


@pytest.mark.asyncio
async def test_vtb_parser_follows_it_landing_to_numeric_career_listing() -> None:
    class LandingClient:
        async def get(self, url: str, **_: object) -> _Response:
            response = _Response()
            if "career-it" in url:
                response.text = (
                    '<a href="https://rabota-vtb.ru/career?department=vtb-4181-teh">'
                    "Откликнуться на вакансии</a>"
                )
            elif url.rstrip("/").endswith("/career") or "department=" in url:
                response.text = '<a href="/career/134519550">Data Engineer</a>'
            else:
                response.text = "<main><h1>Data Engineer</h1></main>"
            return response

    items = [
        item
        async for item in VtbParser().parse(
            CareerSiteSpec(url="https://rabota.vtb.ru/career-it/", limit=5),
            LandingClient(),
        )
    ]
    assert [item.external_id for item in items] == ["134519550"]


@pytest.mark.asyncio
async def test_vtb_parser_accepts_numeric_career_detail_url() -> None:
    response = _Response()
    response.text = '<a href="https://rabota-vtb.ru/career/134519550">Data Engineer</a>'
    client = _Client()
    client.get = lambda url, **kwargs: _response(response)  # type: ignore[method-assign]
    items = [
        item
        async for item in VtbParser().parse(
            CareerSiteSpec(url="https://rabota-vtb.ru/career", limit=5), client
        )
    ]
    assert items[0].external_id == "134519550"


@pytest.mark.asyncio
async def test_employer_parser_hydrates_detail_text() -> None:
    class DetailClient:
        async def get(self, url: str, **_: object) -> _Response:
            response = _Response()
            response.text = (
                '<a href="/career/42">Data Engineer</a>'
                if url.endswith("/career")
                else "<main><h1>Data Engineer</h1><p>Build production ML systems.</p></main>"
            )
            return response

    items = [
        item
        async for item in VtbParser().parse(
            CareerSiteSpec(url="https://rabota-vtb.ru/career", limit=1), DetailClient()
        )
    ]

    assert len(items) == 1
    assert "Build production ML systems" in items[0].text
    assert items[0].metadata["detail_vacancy_confirmed"] is True


@pytest.mark.asyncio
async def test_employer_parser_skips_landing_and_policy_pages() -> None:
    class KonturClient:
        async def get(self, url: str, **_: object) -> _Response:
            response = _Response()
            response.text = (
                (
                    '<a href="https://kontur.ru/career/vacancies/42">ML engineer</a>'
                    '<a href="https://kontur.ru/career/vacancies/conditions">Conditions</a>'
                )
                if url.endswith("/career")
                else "<main><h1>ML engineer</h1></main>"
            )
            return response

    items = [
        item
        async for item in KonturCareerParser().parse(
            CareerSiteSpec(url="https://kontur.ru/career", limit=5), KonturClient()
        )
    ]
    assert [item.external_id for item in items] == ["42"]


@pytest.mark.asyncio
async def test_alfa_parser_uses_company_api() -> None:
    class AlfaClient:
        def __init__(self) -> None:
            self.params: list[object] = []

        async def get(self, url: str, **kwargs: object) -> _Response:
            self.params.append(kwargs.get("params"))
            response = _Response()
            response.json = lambda: {
                "items": [
                    {
                        "id": "105584",
                        "name": "System analyst",
                        "slug": "/moskva/system-analyst_105584",
                        "descriptionText": "Design banking APIs.",
                    }
                ]
            }
            return response

    client = AlfaClient()
    items = [
        item
        async for item in AlfaBankParser().parse(
            CareerSiteSpec(url="https://digital.alfabank.ru/vacancies", limit=1),
            client,
        )
    ]

    assert [item.external_id for item in items] == ["105584"]
    assert "Design banking APIs" in items[0].text
    assert items[0].metadata["company"] == "Альфа-Банк"
    assert client.params
    assert "businessLine" not in str(client.params[0])
    assert "take" in str(client.params[0])


def test_alfa_runtime_defaults_relax_tls() -> None:
    from job_ftch.infrastructure.sources.site_defaults import apply_runtime_defaults

    spec = apply_runtime_defaults(CareerSiteSpec(url="https://job.alfabank.ru/vacancies"))
    assert spec.monitor_config["skip_ssl"] is True


@pytest.mark.asyncio
async def test_uzum_parser_requires_vacancy_detail_path() -> None:
    class UzumClient:
        async def get(self, url: str, **_: object) -> _Response:
            response = _Response()
            response.text = (
                (
                    '<a href="https://people.uzum.com/career/ru">Career</a>'
                    '<a href="https://people.uzum.com/career/ru/vacancies/42">Backend</a>'
                )
                if "vacancies/42" not in url
                else "<main><h1>Backend</h1></main>"
            )
            return response

    items = [
        item
        async for item in UzumCareerParser().parse(
            CareerSiteSpec(url="https://people.uzum.com/career/ru/vacancies", limit=5),
            UzumClient(),
        )
    ]
    assert [item.external_id for item in items] == ["42"]


@pytest.mark.asyncio
async def test_tbc_parser_does_not_emit_vacancies_landing_page() -> None:
    class TbcClient:
        async def get(self, url: str, **_: object) -> _Response:
            response = _Response()
            response.text = (
                (
                    '<a href="https://tbcbank.uz/career/vacancies/">All vacancies</a>'
                    '<a href="https://tbcbank.uz/career/vacancies/42">Backend</a>'
                )
                if url.endswith("/career")
                else "<main><h1>Backend</h1></main>"
            )
            return response

    items = [
        item
        async for item in TbcUzbekistanCareerParser().parse(
            CareerSiteSpec(url="https://tbcbank.uz/career", limit=5), TbcClient()
        )
    ]
    assert [item.external_id for item in items] == ["42"]


@pytest.mark.asyncio
async def test_casib_parser_does_not_treat_news_as_jobs() -> None:
    class CasibClient:
        async def get(self, url: str, **_: object) -> _Response:
            del url
            response = _Response()
            response.text = '<a href="/ru/casib-at-regional-summit-2026-in-astana/">News</a>'
            return response

    items = [
        item
        async for item in CasibCareerParser().parse(
            CareerSiteSpec(url="https://casib.eu/ru/in/news/", limit=5), CasibClient()
        )
    ]
    assert items == []


@pytest.mark.asyncio
async def test_yandex_uz_parser_rejects_non_uzbekistan_vacancy() -> None:
    class YandexUzClient:
        async def get(self, url: str, **_: object) -> _Response:
            response = _Response()
            response.text = (
                '<a href="/jobs/vacancies/backend-42">Backend</a>'
                if "backend-42" not in url
                else "<main><h1>Backend</h1><p>Москва и Санкт-Петербург</p></main>"
            )
            return response

    items = [
        item
        async for item in YandexUzbekistanParser().parse(
            CareerSiteSpec(url="https://yandex.ru/jobs/vacancies/city_tashkent", limit=5),
            YandexUzClient(),
        )
    ]
    assert items == []


@pytest.mark.asyncio
async def test_yandex_uz_discovery_rejects_other_city_landings() -> None:
    class YandexUzClient:
        async def get(self, url: str, **_: object) -> _Response:
            del url
            response = _Response()
            response.text = (
                '<a href="/jobs/vacancies/backend-42">Backend</a>'
                '<a href="/jobs/vacancies/city_saint-petersburg">Saint Petersburg</a>'
            )
            return response

    urls = await YandexUzbekistanParser().discover(
        CareerSiteSpec(url="https://yandex.ru/jobs/vacancies/city_tashkent", limit=5),
        YandexUzClient(),
    )

    assert urls == ["https://yandex.ru/jobs/vacancies/backend-42"]


async def _response(response: _Response) -> _Response:
    return response
