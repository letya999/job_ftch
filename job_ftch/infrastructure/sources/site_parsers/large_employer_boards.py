"""Small HTML adapters for large employer boards without a stable public API."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.browser_utils import navigate, open_page
from job_ftch.infrastructure.sources.career_site import client_for_config
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    DEFAULT_LISTING_MAX_PAGES,
    ListingPagination,
    browser_scroll_collect_urls,
    distinctive_search_tokens,
    keywords_from_spec,
    normalize_search_keywords,
    paginate_listing,
    resolve_browser_config,
    text_matches_keywords,
    with_query_params,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


def _listing_cards_from_html(
    html: str,
    board_url: str,
    href_pattern: re.Pattern[str],
) -> list[tuple[str, str]]:
    page = LexborHTMLParser(html)
    cards: list[tuple[str, str]] = []
    seen: set[str] = set()
    for anchor in page.css("a[href]"):
        href = str(anchor.attributes.get("href") or "").strip()
        url = urljoin(board_url, href)
        if not href_pattern.search(url) or url in seen:
            continue
        if url.rstrip("/") == board_url.rstrip("/"):
            continue
        if url.rsplit("/", 1)[-1].split("?", 1)[0].casefold() in {
            "about",
            "conditions",
            "contacts",
            "privacy",
            "terms",
        }:
            continue
        seen.add(url)
        text = anchor.parent.text(separator="\n", strip=True) if anchor.parent else anchor.text()
        text = "\n".join(part.strip() for part in text.splitlines() if part.strip())
        cards.append((url, text))
    return cards


async def _parse_detail_board(
    spec: CareerSiteSpec,
    client: Any,
    *,
    href_pattern: re.Pattern[str],
    parser_name: str,
    company: str | None = None,
) -> AsyncIterator[RawItem]:
    async def fetch(url: str) -> str:
        response = await client.get(url, follow_redirects=True)
        response.raise_for_status()
        return str(response.text)

    def extract(html: str, url: str) -> list[tuple[str, str]]:
        return _listing_cards_from_html(html, url, href_pattern)

    cards = await paginate_listing(
        fetch,
        extract,
        spec.url,
        limit=spec.limit or 50,
        pagination=ListingPagination(),
        identity=lambda card: card[0],
    )
    keywords = keywords_from_spec(spec)
    emitted = 0
    for url, text in cards:
        if len(text) < 3:
            continue
        if not text_matches_keywords(f"{url}\n{text}", keywords):
            continue
        match = href_pattern.search(url)
        external_id = match.group(1) if match and match.lastindex else url
        try:
            detail_response = await client.get(url, follow_redirects=True)
            detail_response.raise_for_status()
            detail = _extract_detail_text(detail_response.text)
        except (OSError, RuntimeError, ValueError):
            detail = ""
        if detail and detail.casefold() not in text.casefold():
            text = f"{text}\n{detail}"
        try:
            item = build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=spec.source_name or parser_name,
                external_id=external_id,
                url=url,
                text=text,
                metadata={
                    "board_url": spec.url,
                    "parser": parser_name,
                    "observation_kind": "vacancy_detail",
                    "detail_vacancy_confirmed": bool(detail),
                    "company": company,
                    "company_authoritative": bool(company),
                },
            )
        except (TypeError, ValueError):
            continue
        yield item
        emitted += 1
        if spec.limit and emitted >= spec.limit:
            return


async def _discover_detail_board(
    spec: CareerSiteSpec,
    client: Any,
    *,
    href_pattern: re.Pattern[str],
) -> list[str]:
    limit = spec.limit or 50
    try:

        async def fetch(url: str) -> str:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()
            return str(response.text)

        def extract(html: str, url: str) -> list[str]:
            return [card[0] for card in _listing_cards_from_html(html, url, href_pattern)]

        urls = await paginate_listing(
            fetch,
            extract,
            spec.url,
            limit=limit,
            pagination=ListingPagination(),
        )
        if urls:
            return urls[:limit]
    except Exception:  # noqa: BLE001 - browser is the intended fallback
        pass

    bypass_strategy = spec.monitor_config.get("_bypass_strategy")
    browser_config = resolve_browser_config(spec, bypass_strategy)
    async with open_page(browser_config, bypass_strategy=bypass_strategy) as page:
        await navigate(page, spec.url, browser_config)
        return await browser_scroll_collect_urls(
            page,
            getattr(page, "url", spec.url) or spec.url,
            href_pattern,
            limit=limit,
            scroll_loops=5,
            pause_sec=0.5,
        )


def _plain_html(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    return " ".join(LexborHTMLParser(value).text(separator=" ", strip=True).split())


def _extract_detail_text(html: str) -> str:
    page = LexborHTMLParser(html)
    candidates: list[str] = []
    for selector in (
        "article",
        "main",
        "[class*='description']",
        "[id*='description']",
        "body",
    ):
        for node in page.css(selector):
            value = "\n".join(
                part.strip()
                for part in node.text(separator="\n", strip=True).splitlines()
                if part.strip()
            )
            if value:
                candidates.append(value)
    return max(candidates, key=len, default="")


class YadroParser:
    domain_pattern = r"^https?://careers\.yadro\.com(?:/|$)"
    has_custom_parse = True
    supports_discover = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(render=False, wait="domcontentloaded")

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        return await _discover_detail_board(
            spec, client, href_pattern=re.compile(r"/vacancy/(\d+)(?:/)?$")
        )

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        async for item in _parse_detail_board(
            spec,
            client,
            href_pattern=re.compile(r"/vacancy/(\d+)(?:/)?$"),
            parser_name="yadro_board",
            company="YADRO",
        ):
            yield item


_VTB_DETAIL_RE = re.compile(r"/career/(\d+)(?:/)?$")
_VTB_LISTING_RE = re.compile(r"rabota-vtb\.ru/career(?:/?(?:\?|$))", re.IGNORECASE)


def _vtb_has_detail_links(html: str, base_url: str) -> bool:
    page = LexborHTMLParser(html)
    return any(
        _VTB_DETAIL_RE.search(urljoin(base_url, str(anchor.attributes.get("href") or "").strip()))
        for anchor in page.css("a[href]")
    )


def _vtb_listing_fallbacks(html: str, base_url: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for anchor in LexborHTMLParser(html).css("a[href]"):
        href = urljoin(base_url, str(anchor.attributes.get("href") or "").strip()).split("#", 1)[0]
        if not _VTB_LISTING_RE.search(href) or _VTB_DETAIL_RE.search(href):
            continue
        if href in seen:
            continue
        seen.add(href)
        urls.append(href)
    return urls


async def _vtb_listing_spec(spec: CareerSiteSpec, client: Any) -> CareerSiteSpec:
    """Follow the IT landing page through to the numeric career listing."""
    response = await client.get(spec.url, follow_redirects=True)
    response.raise_for_status()
    html = response.text
    current_url = str(getattr(response, "url", spec.url) or spec.url)
    if _vtb_has_detail_links(html, current_url):
        return spec
    for listing_url in _vtb_listing_fallbacks(html, current_url):
        listing_spec = spec.model_copy(update={"url": listing_url})
        listing_response = await client.get(listing_url, follow_redirects=True)
        listing_response.raise_for_status()
        listing_html_url = str(getattr(listing_response, "url", listing_url) or listing_url)
        if _vtb_has_detail_links(listing_response.text, listing_html_url):
            return listing_spec
    return spec


class VtbParser:
    domain_pattern = r"^https?://(?:rabota\.vtb\.ru|rabota-vtb\.ru)(?:/|$)"
    has_custom_parse = True
    supports_discover = False
    confirmed_empty_on_empty = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False,
            wait="domcontentloaded",
            extra={
                "skip_ssl": True,
                "proxy_rescue_allow_domains": ["rabota.vtb.ru", "rabota-vtb.ru"],
            },
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        async with client_for_config(client, spec.monitor_config) as scoped:
            listing_spec = await _vtb_listing_spec(spec, scoped)
            return await _discover_detail_board(listing_spec, scoped, href_pattern=_VTB_DETAIL_RE)

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        async with client_for_config(client, spec.monitor_config) as scoped:
            listing_spec = await _vtb_listing_spec(spec, scoped)
            async for item in _parse_detail_board(
                listing_spec,
                scoped,
                href_pattern=_VTB_DETAIL_RE,
                parser_name="vtb_board",
                company="ВТБ",
            ):
                yield item


class AlfaBankParser:
    domain_pattern = r"^https?://(?:(?:job|digital)\.)?alfabank\.ru(?:/|$)"
    has_custom_parse = True
    supports_discover = False
    supports_search = True
    search_mode = "combined"
    confirmed_empty_on_empty = True
    _API_URL = "https://job.alfabank.ru/api/vacancies"
    _LISTING_URL = "https://job.alfabank.ru/vacancies"

    def build_search_urls(
        self, base_url: str, keywords: Any, *, limit: int | None = None
    ) -> list[str]:
        del base_url, limit
        if not normalize_search_keywords(keywords):
            return []
        return [self._LISTING_URL]

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False,
            wait="domcontentloaded",
            extra={
                "proxy_rescue_allow_domains": ["job.alfabank.ru"],
                "skip_ssl": True,
            },
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        limit = spec.limit or 50
        keywords = keywords_from_spec(spec)
        queries: list[str | None] = [*distinctive_search_tokens(keywords)] or [None]
        page_size = min(50, max(limit, 1))
        seen: set[str] = set()
        emitted = 0
        async with client_for_config(client, spec.monitor_config) as scoped:
            for query in queries:
                skip = 0
                for _page in range(DEFAULT_LISTING_MAX_PAGES):
                    params: dict[str, Any] = {"take": str(page_size), "skip": str(skip)}
                    if query:
                        params["text"] = query
                    response = await scoped.get(
                        self._API_URL,
                        params=params,
                        follow_redirects=True,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    vacancies = payload.get("items", []) if isinstance(payload, dict) else []
                    if not isinstance(vacancies, list) or not vacancies:
                        break
                    for vacancy in vacancies:
                        if not isinstance(vacancy, dict):
                            continue
                        title = str(vacancy.get("name") or "").strip()
                        slug = str(vacancy.get("slug") or "").strip()
                        if not title or not slug:
                            continue
                        external_id = str(vacancy.get("id") or slug)
                        if external_id in seen:
                            continue
                        seen.add(external_id)
                        text = "\n".join(
                            filter(
                                None,
                                (
                                    title,
                                    str(vacancy.get("descriptionText") or "").strip(),
                                    str(vacancy.get("requirements") or "").strip(),
                                    str(vacancy.get("duties") or "").strip(),
                                    str(vacancy.get("conditions") or "").strip(),
                                ),
                            )
                        )
                        if not text_matches_keywords(text, keywords):
                            continue
                        yield build_raw_item(
                            source_kind=SourceKind.CAREER_SITE,
                            source_name=spec.source_name or "alfabank_api",
                            external_id=external_id,
                            url=urljoin("https://job.alfabank.ru", slug),
                            text=text,
                            metadata={
                                "board_url": spec.url,
                                "parser": "alfabank_api",
                                "observation_kind": "vacancy_detail",
                                "detail_vacancy_confirmed": True,
                                "company": "Альфа-Банк",
                                "company_authoritative": True,
                            },
                        )
                        emitted += 1
                        if emitted >= limit:
                            return
                    skip += page_size
                    if len(vacancies) < page_size:
                        break
                    total = payload.get("total") if isinstance(payload, dict) else None
                    if isinstance(total, int) and skip >= total:
                        break


class T1InnotechParser:
    domain_pattern = r"^https?://career\.t1\.ru(?:/|$)"
    has_custom_parse = True
    supports_discover = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(render=True, wait="domcontentloaded")

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        return await _discover_detail_board(
            spec,
            client,
            href_pattern=re.compile(r"/(?:vacanc(?:y|ies)|jobs?)/([^/?#]+)(?:/)?$"),
        )

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        async for item in _parse_detail_board(
            spec,
            client,
            href_pattern=re.compile(r"/(?:vacanc(?:y|ies)|jobs?)/([^/?#]+)(?:/)?$"),
            parser_name="t1_innotech_board",
            company="Т1",
        ):
            yield item


class _EmployerBoardParser:
    """Common detail-link extraction for employer-specific career surfaces."""

    has_custom_parse = True
    supports_discover = True
    detail_pattern = re.compile(
        r"/(?:career|careers|job|jobs|vacanc(?:y|ies)|positions?)/([^/?#]+)(?:/)?$"
    )
    parser_name = "employer_board"
    company: str | None = None

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(render=True, wait="domcontentloaded")

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        return await _discover_detail_board(spec, client, href_pattern=self.detail_pattern)

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        async for item in _parse_detail_board(
            spec,
            client,
            href_pattern=self.detail_pattern,
            parser_name=self.parser_name,
            company=self.company,
        ):
            yield item


class CianCareerParser(_EmployerBoardParser):
    domain_pattern = r"^https?://(?:www\.)?cian\.ru/vacancies(?:/|$)"
    parser_name = "cian_career"
    company = "ЦИАН"
    detail_pattern = re.compile(r"/vacancies/(\d+)(?:/)?$")


class InnotechCareerParser(_EmployerBoardParser):
    domain_pattern = r"^https?://(?:www\.)?inno\.tech(?:/|$)"
    parser_name = "innotech_career"
    company = "Иннотех"


class RostelecomCareerParser(_EmployerBoardParser):
    domain_pattern = r"^https?://job\.rt\.ru(?:/|$)"
    parser_name = "rostelecom_career"
    company = "Ростелеком"
    detail_pattern = re.compile(r"/search/([^/?#]+)(?:/)?$")
    supports_discover = False
    supports_search = True
    search_mode = "combined"
    confirmed_empty_on_empty = True
    _API_URL = "https://job.rt.ru/backend/api/vacancies"
    _LISTING_URL = "https://job.rt.ru/search"

    def build_search_urls(
        self, base_url: str, keywords: Any, *, limit: int | None = None
    ) -> list[str]:
        del base_url, limit
        if not normalize_search_keywords(keywords):
            return []
        return [self._LISTING_URL]

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(render=False, wait="domcontentloaded")

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        keywords = keywords_from_spec(spec)
        limit = spec.limit or 50
        seen: set[str] = set()
        emitted = 0
        max_pages = 20
        for page in range(1, max_pages + 1):
            response = await client.get(
                self._API_URL,
                params={"page": str(page)},
                headers={"Accept": "application/json", "Referer": self._LISTING_URL},
                follow_redirects=True,
            )
            response.raise_for_status()
            payload = response.json()
            vacancies = payload.get("vacancies") if isinstance(payload, dict) else None
            if not isinstance(vacancies, list) or not vacancies:
                return
            for row in vacancies:
                if not isinstance(row, dict):
                    continue
                vacancy_id = row.get("id")
                title = str(row.get("name") or "").strip()
                if not vacancy_id or not title:
                    continue
                external_id = str(vacancy_id)
                if external_id in seen:
                    continue
                seen.add(external_id)
                city = row.get("city")
                city_name = city.get("name") if isinstance(city, dict) else None
                text = "\n".join(
                    part
                    for part in (
                        title,
                        str(city_name or "").strip(),
                        _plain_html(row.get("whatWeToDo")),
                        _plain_html(row.get("whatWeExpect")),
                        _plain_html(row.get("whatWeOffer")),
                    )
                    if part
                )
                if not text_matches_keywords(text, keywords):
                    continue
                yield build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=spec.source_name or self.parser_name,
                    external_id=external_id,
                    url=f"{self._LISTING_URL}/{external_id}",
                    text=text,
                    metadata={
                        "board_url": spec.url,
                        "parser": self.parser_name,
                        "company": self.company,
                        "company_authoritative": True,
                        "detail_vacancy_confirmed": True,
                    },
                )
                emitted += 1
                if emitted >= limit:
                    return
            total_pages = payload.get("totalPages") if isinstance(payload, dict) else None
            if isinstance(total_pages, int) and page >= total_pages:
                return


class MegafonCareerParser(_EmployerBoardParser):
    domain_pattern = r"^https?://career\.megafon\.ru(?:/|$)"
    parser_name = "megafon_career"
    company = "МегаФон"


class PositiveTechnologiesCareerParser(_EmployerBoardParser):
    domain_pattern = r"^https?://(?:www\.)?ptsecurity\.com/about/vacancy(?:/|$)"
    parser_name = "positive_technologies_career"
    company = "Positive Technologies"
    detail_pattern = re.compile(r"/about/vacancy/(?!$)([^/?#]+)(?:/)?$")


class KonturCareerParser(_EmployerBoardParser):
    domain_pattern = r"^https?://(?:www\.)?kontur\.ru/career(?:/|$)"
    parser_name = "kontur_career"
    company = "Контур"


class OneCCareerParser(_EmployerBoardParser):
    domain_pattern = r"^https?://(?:www\.)?1c\.ru/rus/firm1c/vacan(?:/|$)"
    parser_name = "one_career"
    company = "1С"
    detail_pattern = re.compile(r"/rus/firm1c/vacan/vacancy/(\d+)(?:/)?$")


class AstraLinuxCareerParser(_EmployerBoardParser):
    domain_pattern = r"^https?://(?:www\.)?astra\.ru/about/career/vacancies(?:/|$)"
    parser_name = "astra_linux_career"
    company = "Группа Астра"
    detail_pattern = re.compile(r"/about/career/vacancies/([^/?#]+)(?:/)?$")


class SelectelCareerParser(_EmployerBoardParser):
    domain_pattern = r"^https?://(?:www\.)?selectel\.ru/careers(?:/|$)"
    parser_name = "selectel_career"
    company = "Selectel"
    detail_pattern = re.compile(r"/careers/all/vacancy/(\d+)(?:/)?$")


class X5TechCareerParser(_EmployerBoardParser):
    domain_pattern = r"^https?://(?:rabota\.x5\.ru/vacancies|x5-tech\.ru/career)(?:[/?#]|$)"
    parser_name = "x5_tech_career"
    company = "X5 Tech"
    detail_pattern = re.compile(r"/vacancies/([0-9a-f-]{36})(?:/)?$", re.IGNORECASE)
    supports_search = True
    search_mode = "per_keyword"

    def build_search_urls(
        self,
        base_url: str,
        keywords: Any,
        *,
        limit: int | None = None,
    ) -> list[str]:
        del limit
        terms = normalize_search_keywords(keywords)
        if not terms:
            return []
        listing = base_url.split("?", 1)[0]
        return [with_query_params(listing, {"search": term}) for term in terms]


class CsbiCareerParser(_EmployerBoardParser):
    domain_pattern = r"^https?://(?:www\.)?csbi\.ru/job(?:/|$)"
    parser_name = "csbi_career"
    company = "ЦСБИ"


class CasibCareerParser(_EmployerBoardParser):
    domain_pattern = r"^https?://(?:www\.)?casib\.eu(?:/|$)"
    parser_name = "casib_career"
    company = "CASIB"
    detail_pattern = re.compile(r"/(?:career|careers|jobs?|vacanc(?:y|ies))/([^/?#]+)(?:/)?$")


class HalykCareerParser(_EmployerBoardParser):
    domain_pattern = r"^https?://(?:www\.)?halykbank\.kz(?:/|$)"
    parser_name = "halyk_career"
    company = "Halyk Bank"
    detail_pattern = re.compile(r"/(?:[a-z]{2}/)?about/career/vacancies-inner/(\d+)(?:/)?$")
    supports_discover = False
    supports_search = True
    search_mode = "combined"
    confirmed_empty_on_empty = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(render=False, wait="domcontentloaded")

    def build_search_urls(
        self, base_url: str, keywords: Any, *, limit: int | None = None
    ) -> list[str]:
        del limit
        if not normalize_search_keywords(keywords):
            return []
        return [urljoin(base_url, "/about/career/vacancies")]

    @staticmethod
    def _listing_spec(spec: CareerSiteSpec) -> CareerSiteSpec:
        if re.search(r"about/career/vacancies", spec.url, re.IGNORECASE):
            return spec
        return spec.model_copy(update={"url": urljoin(spec.url, "/about/career/vacancies")})

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        listing_spec = self._listing_spec(spec)
        keywords = keywords_from_spec(spec)
        limit = spec.limit or 50

        async def fetch(url: str) -> str:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()
            return str(response.text)

        def extract(html: str, url: str) -> list[tuple[str, str]]:
            return _listing_cards_from_html(html, url, self.detail_pattern)

        cards = await paginate_listing(
            fetch,
            extract,
            listing_spec.url,
            limit=limit,
            pagination=ListingPagination(),
            identity=lambda card: card[0],
        )
        emitted = 0
        for url, text in cards:
            if len(text) < 3 or not text_matches_keywords(f"{url}\n{text}", keywords):
                continue
            match = self.detail_pattern.search(url)
            external_id = match.group(1) if match and match.lastindex else url
            yield build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=spec.source_name or self.parser_name,
                external_id=external_id,
                url=url,
                text=text,
                metadata={
                    "board_url": spec.url,
                    "parser": self.parser_name,
                    "company": self.company,
                    "company_authoritative": True,
                    "detail_vacancy_confirmed": False,
                },
            )
            emitted += 1
            if emitted >= limit:
                return


class FreedomCareerParser(_EmployerBoardParser):
    domain_pattern = r"^https?://job\.freedomholdingcorp\.com(?:/|$)"
    parser_name = "freedom_career"
    company = "Freedom Holding"


class ForteCareerParser(_EmployerBoardParser):
    domain_pattern = r"^https?://career\.forte\.kz(?:/|$)"
    parser_name = "forte_career"
    company = "ForteBank"


class BeelineKazakhstanCareerParser(_EmployerBoardParser):
    domain_pattern = r"^https?://people\.beeline\.kz(?:/|$)"
    parser_name = "beeline_kz_career"
    company = "Beeline Kazakhstan"
    supports_discover = False
    confirmed_empty_on_empty = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(render=False, wait="domcontentloaded")

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        # OutSystems shell has no public listing. Do not fall through to a browser.
        response = await client.get(spec.url, follow_redirects=True)
        response.raise_for_status()
        del response
        return
        yield  # pragma: no cover


class TochkaCareerParser(_EmployerBoardParser):
    domain_pattern = r"^https?://hr\.tochka\.com/vacancies(?:/|$)"
    supports_discover = False
    supports_search = False

    parser_name = "tochka_career"
    company = "Точка Банк"
    detail_pattern = re.compile(r"/vacancies/catalog/([^/?#]+)(?:/)?$")

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(render=False, wait="domcontentloaded")

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        async with client_for_config(client, {"skip_ssl": True}) as insecure_client:
            return await _discover_detail_board(
                spec,
                insecure_client,
                href_pattern=self.detail_pattern,
            )

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        async with client_for_config(client, {"skip_ssl": True}) as insecure_client:
            async for item in _parse_detail_board(
                spec,
                insecure_client,
                href_pattern=self.detail_pattern,
                parser_name=self.parser_name,
                company=self.company,
            ):
                yield item


class YandexUzbekistanParser(_EmployerBoardParser):
    domain_pattern = r"^https?://yandex\.ru/jobs/vacancies/city_tashkent(?:/|$)"
    parser_name = "yandex_uz_jobs"
    company = "Яндекс"
    detail_pattern = re.compile(r"/jobs/vacancies/(?!city_[^/?#]+(?:/|$))([^/?#]+)(?:/)?$")

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        async for item in super().parse(spec, client):
            if re.search(r"\b(?:ташкент\w*|tashkent|узбекистан\w*|uzbekistan)\b", item.text, re.I):
                metadata = {**item.metadata, "country": "Узбекистан", "country_authoritative": True}
                yield item.model_copy(update={"metadata": metadata})


class UzumCareerParser(_EmployerBoardParser):
    domain_pattern = r"^https?://people\.uzum\.com/career(?:/|$)"
    parser_name = "uzum_career"
    company = "Uzum"
    detail_pattern = re.compile(r"/career/[^/?#]+/(?:vacanc(?:y|ies)|job|jobs)/([^/?#]+)(?:/)?$")


class ClickCareerParser(_EmployerBoardParser):
    domain_pattern = r"^https?://(?:www\.)?click\.uz/(?:ru/)?(?:career|vacancies)(?:/|$)"
    parser_name = "click_career"
    company = "CLICK"


class TbcUzbekistanCareerParser(_EmployerBoardParser):
    domain_pattern = r"^https?://(?:www\.)?tbcbank\.uz/career(?:/|$)"
    parser_name = "tbc_uz_career"
    company = "TBC Uzbekistan"
    detail_pattern = re.compile(r"/career/vacancies/([^/?#]+)(?:/)?$")


class HirefiParser(_EmployerBoardParser):
    domain_pattern = r"^https?://(?:www\.)?hirefi\.io(?:/|$)"
    parser_name = "hirefi"
    company = "HireFi"


register_site_parser("yadro", domain_pattern=YadroParser.domain_pattern)(YadroParser)
register_site_parser("vtb", domain_pattern=VtbParser.domain_pattern)(VtbParser)
register_site_parser("alfa_bank", domain_pattern=AlfaBankParser.domain_pattern)(AlfaBankParser)
register_site_parser("t1_innotech", domain_pattern=T1InnotechParser.domain_pattern)(
    T1InnotechParser
)
for _parser_class in (
    CianCareerParser,
    InnotechCareerParser,
    RostelecomCareerParser,
    MegafonCareerParser,
    PositiveTechnologiesCareerParser,
    KonturCareerParser,
    OneCCareerParser,
    AstraLinuxCareerParser,
    SelectelCareerParser,
    X5TechCareerParser,
    CsbiCareerParser,
    CasibCareerParser,
    HalykCareerParser,
    FreedomCareerParser,
    ForteCareerParser,
    BeelineKazakhstanCareerParser,
    TochkaCareerParser,
    YandexUzbekistanParser,
    UzumCareerParser,
    ClickCareerParser,
    TbcUzbekistanCareerParser,
    HirefiParser,
):
    register_site_parser(_parser_class.parser_name, domain_pattern=_parser_class.domain_pattern)(
        _parser_class
    )
