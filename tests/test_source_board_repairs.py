import httpx
import pytest

from job_ftch.application.registry import resolve_site_parser
from job_ftch.domain import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.gazprombank import _extract_listing
from job_ftch.infrastructure.sources.site_parsers.hh_employer_fallbacks import (
    AlfaBankHhFallback,
    TwoGisHhFallback,
    parse_hh_employer_api,
)
from job_ftch.infrastructure.sources.site_parsers.kolesa import (
    _nuxt_vacancies_are_explicitly_empty,
)
from job_ftch.infrastructure.sources.site_parsers.large_employer_boards import TochkaCareerParser
from job_ftch.infrastructure.sources.site_parsers.ozon import OzonCareerParser
from job_ftch.infrastructure.sources.site_parsers.tele2_kz import _extract_hh_employer_url


def test_kolesa_recognizes_authoritative_empty_nuxt_listing() -> None:
    html = (
        '<script id="__NUXT_DATA__">'
        '[["Reactive",1],{"data":2},{"vacancy-list":3},{}]'
        "</script>"
    )

    assert _nuxt_vacancies_are_explicitly_empty(html) is True


def test_gazprombank_recognizes_authoritative_empty_next_listing() -> None:
    html = (
        '<script id="__NEXT_DATA__">'
        '{"props":{"pageProps":{"json":{"vacancies":[],"total":0}}}}'
        "</script>"
    )

    assert _extract_listing(html, "https://www.gazprombank.tech/vacancies/") == (0, [])


def test_tele2_extracts_linked_hh_employer_board() -> None:
    page = (
        '"https://almaty.hh.kz/employer/111304?dpt=111304-business'
        r"\u0026hhtmFrom=vacancy_search_list\""
    )

    assert _extract_hh_employer_url(page) == (
        "https://almaty.hh.kz/employer/111304?dpt=111304-business"
        "&hhtmFrom=vacancy_search_list"
    )


def test_unreachable_company_boards_resolve_to_official_fallbacks() -> None:
    assert isinstance(resolve_site_parser("https://job.2gis.ru/vacancies"), TwoGisHhFallback)
    assert isinstance(
        resolve_site_parser("https://job.alfabank.ru/vacancies/digital"),
        AlfaBankHhFallback,
    )
    ozon = resolve_site_parser("https://career.ozon.ru/")
    assert isinstance(ozon, OzonCareerParser)
    assert ozon.employer_url.startswith("https://hh.ru/employer/2180")
    assert isinstance(
        resolve_site_parser("https://hr.tochka.com/vacancies/it/"),
        TochkaCareerParser,
    )


@pytest.mark.asyncio
async def test_hh_employer_api_builds_structured_source_items() -> None:
    class _Client:
        async def get(self, url: str, **kwargs: object) -> httpx.Response:
            assert url == "https://api.hh.ru/vacancies"
            assert kwargs["params"] == {
                "employer_id": "64174",
                "per_page": 5,
                "order_by": "publication_time",
            }
            return httpx.Response(
                200,
                request=httpx.Request("GET", url),
                json={
                    "items": [
                        {
                            "id": "123",
                            "name": "Python-разработчик",
                            "alternate_url": "https://hh.ru/vacancy/123",
                            "published_at": "2026-08-30T10:00:00+0300",
                            "employer": {"name": "2ГИС"},
                            "area": {"name": "Москва"},
                            "snippet": {
                                "requirement": "Python и <highlighttext>SQL</highlighttext>",
                                "responsibility": "Разрабатывать сервисы",
                            },
                        }
                    ]
                },
            )

    spec = CareerSiteSpec(
        url="https://job.2gis.ru/vacancies",
        source_name="job_2gis_ru_vacancies",
        limit=5,
    )
    items = [item async for item in parse_hh_employer_api(spec, _Client(), employer_id="64174")]

    assert len(items) == 1
    assert items[0].external_id == "123"
    assert "Python и SQL" in items[0].text
    assert items[0].metadata["parser"] == "hh_employer_api"
