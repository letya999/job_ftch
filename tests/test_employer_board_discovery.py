from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from structlog.testing import capture_logs

from job_ftch.application.registry import resolve_site_parser
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.large_employer_boards import (
    CianCareerParser,
    _cian_listing_html,
    _discover_detail_board,
    _listing_cards_from_html,
)


def test_listing_cards_match_query_string_detail_urls() -> None:
    html = '<a href="/vacancies/python-developer?from=list">Python Developer</a>'
    cards = _listing_cards_from_html(
        html,
        "https://career.t1.ru/",
        re.compile(r"/vacancies/([^/?#]+)(?:/)?$"),
    )
    assert [url for url, _text in cards] == [
        "https://career.t1.ru/vacancies/python-developer?from=list"
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://career.cian.ru/",
        "https://career.cian.ru/vacancies",
        "https://www.cian.ru/vacancies/",
    ],
)
def test_cian_parser_binds_career_subdomain(url: str) -> None:
    parser = resolve_site_parser(url)
    assert parser is not None
    assert isinstance(parser, CianCareerParser)


@pytest.mark.asyncio
async def test_discover_detail_board_uses_detail_limit_not_emit_limit() -> None:
    html = """
    <html><body>
      <a href="/vacancies/one">One</a>
      <a href="/vacancies/two">Two</a>
      <a href="/vacancies/three">Three</a>
    </body></html>
    """

    class _Client:
        async def get(self, url: str, follow_redirects: bool = True) -> SimpleNamespace:
            del url, follow_redirects
            return SimpleNamespace(text=html, raise_for_status=lambda: None)

    spec = CareerSiteSpec(
        url="https://career.t1.ru/vacancies",
        limit=1,
        detail_limit=2,
    )
    urls = await _discover_detail_board(
        spec,
        _Client(),
        href_pattern=re.compile(r"/vacancies/([^/?#]+)(?:/)?$"),
    )
    assert urls == [
        "https://career.t1.ru/vacancies/one",
        "https://career.t1.ru/vacancies/two",
    ]


@pytest.mark.asyncio
async def test_cian_listing_html_raises_on_smartcaptcha() -> None:
    from job_ftch.infrastructure.sources.monitors.shared import BrowserChallengeError

    html = (
        '<html><body><div class="smart-captcha" data-sitekey="ysc1_abc">'
        "Yandex SmartCaptcha</div></body></html>"
    )

    class _Client:
        async def get(self, url: str, follow_redirects: bool = True) -> SimpleNamespace:
            del follow_redirects
            return SimpleNamespace(
                text=html,
                url="https://career.cian.ru/tmgrdfrend/showcaptcha",
                status_code=200,
                headers={},
                content=html.encode(),
                raise_for_status=lambda: None,
            )

    with capture_logs() as logs, pytest.raises(BrowserChallengeError) as exc_info:
        await _cian_listing_html(_Client(), "https://career.cian.ru/", "career.cian.ru")
    assert exc_info.value.challenge_type == "smartcaptcha"
    encounter = next(entry for entry in logs if entry.get("event") == "captcha_encounter")
    assert encounter["captcha_type"] == "smartcaptcha"
    assert encounter["captcha_host"] == "career.cian.ru"
    assert encounter["captcha_outcome"] == "observed"


@pytest.mark.asyncio
async def test_cian_listing_html_drops_classifieds_host() -> None:
    from job_ftch.infrastructure.sources.monitors.shared import ListingHostMismatchError

    class _Client:
        async def get(self, url: str, follow_redirects: bool = True) -> SimpleNamespace:
            del follow_redirects
            return SimpleNamespace(
                text="<html><body><a href='/cat.php'>квартира</a></body></html>",
                url="https://www.cian.ru/",
                status_code=200,
                headers={},
                content=b"",
                raise_for_status=lambda: None,
            )

    with pytest.raises(ListingHostMismatchError) as exc_info:
        await _cian_listing_html(_Client(), "https://career.cian.ru/", "career.cian.ru")
    assert exc_info.value.origin_host == "career.cian.ru"
    assert exc_info.value.final_host == "www.cian.ru"


@pytest.mark.asyncio
async def test_cian_discover_reads_numeric_vacancy_cards() -> None:
    html = """
    <html><body>
      <a href="/vacancies/12345">Python Developer</a>
      <a href="/vacancies/67890">ML Engineer</a>
    </body></html>
    """

    class _Client:
        async def get(self, url: str, follow_redirects: bool = True) -> SimpleNamespace:
            del follow_redirects
            return SimpleNamespace(
                text=html,
                url="https://career.cian.ru/vacancies",
                status_code=200,
                headers={},
                content=html.encode(),
                raise_for_status=lambda: None,
            )

    parser = CianCareerParser()
    urls = await parser.discover(
        CareerSiteSpec(url="https://career.cian.ru/", limit=10),
        _Client(),
    )
    assert urls == [
        "https://career.cian.ru/vacancies/12345",
        "https://career.cian.ru/vacancies/67890",
    ]


@pytest.mark.asyncio
async def test_cian_discover_retries_extra_candidate_after_host_hop() -> None:
    vacancy_html = """
    <html><body>
      <a href="/vacancies/12345">Python Developer</a>
    </body></html>
    """

    class _Client:
        async def get(self, url: str, follow_redirects: bool = True) -> SimpleNamespace:
            del follow_redirects
            if url.rstrip("/") == "https://career.cian.ru":
                return SimpleNamespace(
                    text="<html><body><a href='/cat.php'>квартира</a></body></html>",
                    url="https://www.cian.ru/",
                    status_code=200,
                    headers={},
                    content=b"",
                    raise_for_status=lambda: None,
                )
            return SimpleNamespace(
                text=vacancy_html,
                url="https://career.cian.ru/vacancies",
                status_code=200,
                headers={},
                content=vacancy_html.encode(),
                raise_for_status=lambda: None,
            )

    parser = CianCareerParser()
    urls = await parser.discover(
        CareerSiteSpec(url="https://career.cian.ru/", limit=10),
        _Client(),
    )
    assert urls == ["https://career.cian.ru/vacancies/12345"]


@pytest.mark.asyncio
async def test_cian_discover_raises_on_smartcaptcha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.infrastructure.sources.monitors.shared import BrowserChallengeError

    html = (
        '<html><body><div class="smart-captcha" data-sitekey="ysc1_abc">'
        "Yandex SmartCaptcha</div></body></html>"
    )

    class _Client:
        async def get(self, url: str, follow_redirects: bool = True) -> SimpleNamespace:
            del follow_redirects
            return SimpleNamespace(
                text=html,
                url="https://career.cian.ru/tmgrdfrend/showcaptcha",
                status_code=200,
                headers={},
                content=html.encode(),
                raise_for_status=lambda: None,
            )

    called = {"discover": False}

    async def _fake_discover(
        spec: CareerSiteSpec, client: object, *, href_pattern: object
    ) -> list[str]:
        del spec, client, href_pattern
        called["discover"] = True
        return ["https://career.cian.ru/vacancies/12345"]

    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.site_parsers.large_employer_boards._discover_detail_board",
        _fake_discover,
    )
    parser = CianCareerParser()
    with pytest.raises(BrowserChallengeError) as exc_info:
        await parser.discover(
            CareerSiteSpec(url="https://career.cian.ru/", limit=10),
            _Client(),
        )
    assert exc_info.value.challenge_type == "smartcaptcha"
    assert called["discover"] is False


@pytest.mark.asyncio
async def test_cian_parse_drops_classifieds_host() -> None:
    from job_ftch.infrastructure.sources.monitors.shared import ListingHostMismatchError

    class _Client:
        async def get(self, url: str, follow_redirects: bool = True) -> SimpleNamespace:
            del follow_redirects
            return SimpleNamespace(
                text="<html><body><a href='/cat.php'>квартира</a></body></html>",
                url="https://www.cian.ru/",
                status_code=200,
                headers={},
                content=b"",
                raise_for_status=lambda: None,
            )

    parser = CianCareerParser()
    with pytest.raises(ListingHostMismatchError) as exc_info:
        _ = [
            item
            async for item in parser.parse(
                CareerSiteSpec(url="https://career.cian.ru/", limit=10),
                _Client(),
            )
        ]
    assert exc_info.value.kind == "listing_redirected"
    assert parser.confirmed_empty_on_empty is True


@pytest.mark.asyncio
async def test_cian_parse_raises_on_smartcaptcha() -> None:
    from job_ftch.infrastructure.sources.monitors.shared import BrowserChallengeError

    html = (
        '<html><body><div class="smart-captcha" data-sitekey="ysc1_abc">'
        "Yandex SmartCaptcha</div></body></html>"
    )

    class _Client:
        async def get(self, url: str, follow_redirects: bool = True) -> SimpleNamespace:
            del follow_redirects
            return SimpleNamespace(
                text=html,
                url="https://www.cian.ru/cian-captcha/?redirect_url=https://career.cian.ru/",
                status_code=200,
                headers={},
                content=html.encode(),
                raise_for_status=lambda: None,
            )

    parser = CianCareerParser()
    with pytest.raises(BrowserChallengeError) as exc_info:
        _ = [
            item
            async for item in parser.parse(
                CareerSiteSpec(url="https://career.cian.ru/", limit=10),
                _Client(),
            )
        ]
    assert exc_info.value.challenge_type == "smartcaptcha"
