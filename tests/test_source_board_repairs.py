from job_ftch.infrastructure.sources.site_parsers.gazprombank import _extract_listing
from job_ftch.infrastructure.sources.site_parsers.kolesa import (
    _nuxt_vacancies_are_explicitly_empty,
)
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
