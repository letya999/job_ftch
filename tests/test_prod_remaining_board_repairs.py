from __future__ import annotations

import pytest

from job_ftch.application.registry import resolve_site_parser
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.monitors.shared import BrowserChallengeError
from job_ftch.infrastructure.sources.site_parsers.bcc import BccCareerParser
from job_ftch.infrastructure.sources.site_parsers.gorodrabot import GorodRabotParser
from job_ftch.infrastructure.sources.site_parsers.indrive import InDriveCareerParser
from job_ftch.infrastructure.sources.site_parsers.large_employer_boards import (
    BeelineKazakhstanCareerParser,
    HalykCareerParser,
    RostelecomCareerParser,
    VtbParser,
)
from job_ftch.infrastructure.sources.site_parsers.qyzmet import QyzmetParser
from job_ftch.infrastructure.sources.site_parsers.rabota_kz import RabotaKzParser
from job_ftch.infrastructure.sources.site_parsers.superjob import SuperJobRuParser
from job_ftch.infrastructure.sources.site_parsers.t2 import T2CareerParser


class _Response:
    def __init__(self, text: str = "", payload: object | None = None, url: str = "") -> None:
        self.text = text
        self.url = url
        self.status_code = 200
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class _Client:
    def __init__(self, responses: dict[str, _Response]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    async def get(self, url: str, **_: object) -> _Response:
        self.calls.append(url)
        for prefix, response in self._responses.items():
            if url == prefix or url.startswith(prefix):
                if not response.url:
                    response.url = url
                return response
        raise AssertionError(url)


def test_remaining_prod_boards_resolve_to_http_parsers() -> None:
    cases = (
        ("https://rabota.kz/", RabotaKzParser),
        ("https://www.bcc.kz/career/vacancies/", BccCareerParser),
        ("https://kazahstan.gorodrabot.kz/", GorodRabotParser),
        ("https://careers.t2.ru/", T2CareerParser),
        ("https://careers.indrive.com/vacancies/", InDriveCareerParser),
        ("https://qyzmet.kz/", QyzmetParser),
        ("https://people.beeline.kz/", BeelineKazakhstanCareerParser),
        ("https://halykbank.kz/kz/about/career/vacancies", HalykCareerParser),
        ("https://rabota.vtb.ru/career-it/", VtbParser),
        ("https://job.rt.ru/", RostelecomCareerParser),
        ("https://www.superjob.ru/vacancy/search/", SuperJobRuParser),
    )
    for url, cls in cases:
        parser = resolve_site_parser(url)
        assert isinstance(parser, cls), url
        assert parser.supports_discover is False, url
        assert getattr(parser, "confirmed_empty_on_empty", False) is True, url


def test_qyzmet_search_url_uses_vacancies_path() -> None:
    urls = QyzmetParser().build_search_urls("https://qyzmet.kz/", ["LLM Engineer"])
    assert urls == ["https://qyzmet.kz/вакансии"]


def test_gorodrabot_search_url_stays_on_homepage() -> None:
    urls = GorodRabotParser().build_search_urls(
        "https://kazahstan.gorodrabot.kz/", ["LLM Engineer"]
    )
    assert urls == ["https://kazahstan.gorodrabot.kz/"]


@pytest.mark.asyncio
async def test_rabota_kz_emits_job_list_cards() -> None:
    job_id = "a" * 16
    html = f"""
    <a href="/job/list/{job_id}">ML Engineer в Астане</a>
    <a href="/job/list/{job_id}">Развернуть</a>
    """
    items = [
        item
        async for item in RabotaKzParser().parse(
            CareerSiteSpec(url="https://rabota.kz/", source_name="rabota_kz", limit=5),
            _Client({"https://rabota.kz/job/list": _Response(html)}),
        )
    ]
    assert len(items) == 1
    assert items[0].external_id == job_id
    assert "ML Engineer" in items[0].text


@pytest.mark.asyncio
async def test_bcc_emits_numeric_career_cards() -> None:
    html = '<a href="/career/41">Главный специалист Data Engineer</a>'
    items = [
        item
        async for item in BccCareerParser().parse(
            CareerSiteSpec(url="https://www.bcc.kz/career/vacancies/", source_name="bcc", limit=5),
            _Client({"https://www.bcc.kz/career/vacancies/": _Response(html)}),
        )
    ]
    assert len(items) == 1
    assert items[0].external_id == "41"
    assert "Data Engineer" in items[0].text


@pytest.mark.asyncio
async def test_indrive_emits_job_cards_from_data_url() -> None:
    html = """
    <a class="c-job-card" data-id="7329"
       href="https://indrive.pinpointhq.com/en/postings/example-posting"
       data-url="https://careers.indrive.com/vacancies/abc123/">
      Senior DevOps Engineer Fully remote
    </a>
    """
    items = [
        item
        async for item in InDriveCareerParser().parse(
            CareerSiteSpec(
                url="https://careers.indrive.com/vacancies/", source_name="indrive", limit=5
            ),
            _Client({"https://careers.indrive.com/vacancies/": _Response(html)}),
        )
    ]
    assert len(items) == 1
    assert items[0].external_id == "7329"
    assert str(items[0].url).endswith("/vacancies/abc123/")


@pytest.mark.asyncio
async def test_halyk_emits_listing_cards_without_detail_fetch() -> None:
    html = """
    <a href="/about/career/vacancies-inner/1">Головной банк Алматы Red Team Expert</a>
    """
    listing = "https://halykbank.kz/kz/about/career/vacancies"
    client = _Client({listing: _Response(html)})
    items = [
        item
        async for item in HalykCareerParser().parse(
            CareerSiteSpec(
                url="https://halykbank.kz/kz/about/career/vacancies",
                source_name="halyk",
                limit=5,
            ),
            client,
        )
    ]
    assert len(items) == 1
    assert items[0].external_id == "1"
    assert "Red Team Expert" in items[0].text
    assert all("vacancies-inner" not in url or url.endswith("/vacancies") for url in client.calls)


@pytest.mark.asyncio
async def test_rostelecom_parses_backend_api() -> None:
    payload = {
        "totalPages": 1,
        "totalCount": 1,
        "vacancies": [
            {
                "id": 14418,
                "name": "ML Engineer",
                "city": {"name": "г. Москва"},
                "whatWeToDo": "<p>Train LLM models.</p>",
            }
        ],
    }
    items = [
        item
        async for item in RostelecomCareerParser().parse(
            CareerSiteSpec(url="https://job.rt.ru/search", source_name="rt", limit=5),
            _Client(
                {
                    "https://job.rt.ru/backend/api/vacancies": _Response(
                        payload=payload, url="https://job.rt.ru/backend/api/vacancies"
                    )
                }
            ),
        )
    ]
    assert len(items) == 1
    assert items[0].external_id == "14418"
    assert "Train LLM models" in items[0].text


@pytest.mark.asyncio
async def test_superjob_raises_on_challenge_page() -> None:
    with pytest.raises(BrowserChallengeError):
        _ = [
            item
            async for item in SuperJobRuParser().parse(
                CareerSiteSpec(
                    url="https://www.superjob.ru/vacancy/search/?keywords=LLM",
                    source_name="superjob",
                    limit=5,
                ),
                _Client(
                    {
                        "https://www.superjob.ru/vacancy/search/?keywords=LLM": _Response(
                            "<html>smartcaptcha</html>"
                        )
                    }
                ),
            )
        ]


@pytest.mark.asyncio
async def test_superjob_emits_vacancy_detail_links() -> None:
    html = (
        '<a href="/vakansii/senior-python-razrabotchik-54239872.html">'
        "Senior Python / ML Engineer</a>"
    )
    items = [
        item
        async for item in SuperJobRuParser().parse(
            CareerSiteSpec(
                url="https://www.superjob.ru/vacancy/search/?keywords=ML",
                source_name="superjob",
                limit=5,
            ),
            _Client({"https://www.superjob.ru/vacancy/search/?keywords=ML": _Response(html)}),
        )
    ]
    assert len(items) == 1
    assert items[0].external_id == "54239872"


@pytest.mark.asyncio
async def test_beeline_kz_confirmed_empty_without_browser() -> None:
    items = [
        item
        async for item in BeelineKazakhstanCareerParser().parse(
            CareerSiteSpec(url="https://people.beeline.kz/", source_name="beeline_kz", limit=5),
            _Client({"https://people.beeline.kz/": _Response("<html></html>")}),
        )
    ]
    assert items == []
