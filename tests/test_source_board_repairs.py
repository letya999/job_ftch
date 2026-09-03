import pytest

from job_ftch.application.registry import resolve_site_parser
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.beeline import BeelineRuParser
from job_ftch.infrastructure.sources.site_parsers.gazprombank import _extract_listing
from job_ftch.infrastructure.sources.site_parsers.hh_employer_fallbacks import (
    TwoGisCareerParser,
)
from job_ftch.infrastructure.sources.site_parsers.kolesa import (
    _nuxt_vacancies_are_explicitly_empty,
)
from job_ftch.infrastructure.sources.site_parsers.large_employer_boards import (
    AlfaBankParser,
    TochkaCareerParser,
)
from job_ftch.infrastructure.sources.site_parsers.ozon import OzonCareerParser
from job_ftch.infrastructure.sources.site_parsers.tele2_kz import _extract_hh_employer_url


def test_kolesa_recognizes_authoritative_empty_nuxt_listing() -> None:
    html = '<script id="__NUXT_DATA__">[["Reactive",1],{"data":2},{"vacancy-list":3},{}]</script>'

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
        "https://almaty.hh.kz/employer/111304?dpt=111304-business&hhtmFrom=vacancy_search_list"
    )


def test_company_boards_resolve_to_company_owned_parsers() -> None:
    assert isinstance(resolve_site_parser("https://job.2gis.ru/vacancies"), TwoGisCareerParser)
    assert isinstance(
        resolve_site_parser("https://job.alfabank.ru/vacancies/digital"),
        AlfaBankParser,
    )
    ozon = resolve_site_parser("https://career.ozon.ru/")
    assert isinstance(ozon, OzonCareerParser)
    assert isinstance(
        resolve_site_parser("https://hr.tochka.com/vacancies/it/"),
        TochkaCareerParser,
    )
    assert resolve_site_parser("https://job.2gis.ru/vacancies").supports_search is False
    assert resolve_site_parser("https://job.2gis.ru/vacancies").supports_discover is False
    assert resolve_site_parser("https://career.ozon.ru/").supports_search is True
    assert isinstance(resolve_site_parser("https://job.beeline.ru/vacancies"), BeelineRuParser)
    assert isinstance(resolve_site_parser("https://jobs.beeline.ru/"), BeelineRuParser)
    assert BeelineRuParser().build_search_urls(
        "https://job.beeline.ru/vacancies", ["LLM Engineer"]
    ) == ["https://job.beeline.ru/vacancies"]
    from job_ftch.infrastructure.sources.site_parsers.t2 import T2CareerParser

    t2 = resolve_site_parser("https://careers.t2.ru/")
    assert isinstance(t2, T2CareerParser)
    assert t2.supports_discover is False
    assert T2CareerParser().build_search_urls("https://careers.t2.ru/", ["LLM Engineer"]) == [
        "https://careers.t2.ru/"
    ]
    assert resolve_site_parser("https://job.tele2.kz/").supports_search is False
    assert resolve_site_parser("https://hr.tochka.com/vacancies/it/").supports_search is False


def test_ozon_career_defaults_stay_http_only() -> None:
    from job_ftch.infrastructure.sources.site_defaults import apply_runtime_defaults

    spec = apply_runtime_defaults(CareerSiteSpec(url="https://career.ozon.ru/"))
    assert spec.monitor_config.get("render") is False
    assert getattr(OzonCareerParser(), "confirmed_empty_on_empty", False) is True


@pytest.mark.asyncio
async def test_ozon_queries_distinctive_tokens_from_roles() -> None:
    captured: list[object] = []

    class _Client:
        async def get(self, url: str, **kwargs: object) -> object:
            del url
            captured.append(kwargs.get("params"))

            class _Response:
                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict[str, object]:
                    return {
                        "items": [
                            {
                                "internalUuid": "abc",
                                "title": "Старший Data Scientist, Факторы ранжирования LLM",
                                "department": "ML",
                                "city": "Москва",
                                "professionalRoles": [],
                                "description": "LLM ranking",
                            }
                        ],
                        "meta": {"page": 1, "totalItems": 1},
                    }

            return _Response()

    items = [
        item
        async for item in OzonCareerParser().parse(
            CareerSiteSpec(
                url="https://career.ozon.ru/vacancy/",
                source_name="ozon",
                limit=1,
                monitor_config={"_search_keywords": ["LLM Engineer"]},
            ),
            _Client(),
        )
    ]

    assert captured
    assert captured[0] == {"meta.limit": 1, "meta.page": 1, "query": "LLM"}
    assert [item.external_id for item in items] == ["abc"]
