from job_ftch.application.registry import resolve_site_parser
from job_ftch.infrastructure.sources.site_parsers.gazprombank import _extract_listing
from job_ftch.infrastructure.sources.site_parsers.hh_employer_fallbacks import (
    AlfaBankHhFallback,
    TwoGisHhFallback,
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
