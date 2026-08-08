"""Site parsers for Ozon career surfaces.

SPA — needs a real browser. Provide `render=True` and `wait="domcontentloaded"`
runtime defaults so the default monitor chain knows to spin up Playwright.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    effective_limit,
    resolve_browser_config,
)
from job_ftch.infrastructure.sources.source_deadline import await_with_source_deadline

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

_URL_FILTER = r"ozon\.tech/vacancies/[a-f0-9\-]+-[a-z0-9-]+/?$"
_OZON_JOB_PATTERN = r"^https?://(?:job|career)\.ozon\.ru(?:/|$)"
_OZON_VACANCY_URL = "https://career.ozon.ru/vacancy/"


class OzonTechParser:
    domain_pattern = r"^https?://ozon\.tech/vacancies"
    has_custom_parse = False  # runtime-defaults only

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            url_filter=_URL_FILTER,
            render=True,
            wait="domcontentloaded",
            include_if_detail_page=False,
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        del spec, client
        return
        yield  # pragma: no cover

    @property
    def __name__(self) -> str:
        return "OzonTechParser"


register_site_parser(
    "ozon_tech",
    domain_pattern=OzonTechParser.domain_pattern,
    assessment_hint=known_board_assessment_hint("known_site", "site_parser:ozon.tech"),
)(OzonTechParser)


class OzonCareerParser:
    domain_pattern = _OZON_JOB_PATTERN
    has_custom_parse = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=True,
            wait="domcontentloaded",
            include_if_detail_page=False,
            extra={"canonical_url": _OZON_VACANCY_URL},
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        del client
        from job_ftch.config import get_settings
        from job_ftch.infrastructure.sources.browser_utils import BROWSER_KEYS, open_page

        limit = max(1, effective_limit(spec, get_settings()))
        bypass_strategy = spec.monitor_config.get("_bypass_strategy")
        browser_config = resolve_browser_config(
            spec,
            bypass_strategy,
            defaults={"wait": "domcontentloaded", "headless": True, "stealth": True},
        )
        for key, value in spec.monitor_config.items():
            if key in BROWSER_KEYS:
                browser_config[key] = value
        target_url = _OZON_VACANCY_URL
        async with open_page(browser_config, bypass_strategy=bypass_strategy) as page:
            await await_with_source_deadline(
                page.goto(
                    target_url,
                    wait_until="domcontentloaded",
                    timeout=int(get_settings().career_site_timeout_seconds * 1000),
                )
            )
            await await_with_source_deadline(
                page.wait_for_selector('a[href*="/vacancy/"]', timeout=10_000)
            )
            vacancies = await page.eval_on_selector_all(
                'a[href*="/vacancy/"]',
                """
                els => els.map(a => ({
                    text: (a.innerText || a.textContent || '').trim(),
                    href: a.href
                })).filter(x => x.text && x.href)
                """,
            )

        source_name = spec.source_name or "ozon_careers"
        seen: set[str] = set()
        for vacancy in vacancies:
            href = str(vacancy.get("href") or "").strip()
            title = " ".join(str(vacancy.get("text") or "").split())
            if not href or not title or href in seen:
                continue
            path = urlparse(href).path.strip("/")
            if path in {"vacancy", "vacancy/"}:
                continue
            seen.add(href)
            yield build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=source_name,
                external_id=path or href,
                url=href,
                text=title,
                metadata={
                    "board_url": target_url,
                    "job_url": href,
                    "parser": "ozon_career_dom",
                    "adapter": "ozon_career_dom",
                    "soft_403_with_content": True,
                },
            )
            if len(seen) >= limit:
                break


register_site_parser(
    "ozon_career",
    domain_pattern=OzonCareerParser.domain_pattern,
    assessment_hint=known_board_assessment_hint("known_site", "site_parser:ozon_career_dom"),
)(OzonCareerParser)
