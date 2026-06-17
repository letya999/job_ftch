"""Site-specific parser for yandex.ru/jobs.

Yandex Jobs is a Next.js SPA that loads vacancy data via a paginated REST API
(/jobs/api/publications). The DOM shows only ~60 cards via infinite scroll, but
the API returns 200+ results across 12+ cursor pages.

Strategy: open the page in a browser, intercept all /api/publications responses,
collect every vacancy from the API payloads. This gives us the full catalogue
without fighting infinite-scroll timing.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

import structlog

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

logger = structlog.get_logger(__name__)

_API_PATH = "/jobs/api/publications"


def _clean_text(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.split())


def _item_from_api(payload: dict[str, Any], base_url: str) -> RawItem | None:
    """Convert a single /api/publications result dict into a RawItem."""
    title = _clean_text(payload.get("title"))
    if not title:
        return None

    slug = payload.get("publication_slug_url") or ""
    vacancy_id = payload.get("id")
    job_url = f"{base_url.rstrip('/')}/{slug}" if slug else base_url

    # Extract structured metadata from nested API objects
    vacancy = payload.get("vacancy") or {}
    cities = [c.get("name", "") for c in vacancy.get("cities", []) if c.get("name")]
    skills = [s.get("name", "") for s in vacancy.get("skills", []) if s.get("name")]
    work_modes = [w.get("name", "") for w in vacancy.get("work_modes", []) if w.get("name")]

    public_service = payload.get("public_service") or {}
    service_name = public_service.get("name", "")
    service_group = (public_service.get("group") or {}).get("name", "")

    short_summary = _clean_text(payload.get("short_summary"))

    text_parts = [title, short_summary, service_name, service_group, *cities, *skills]
    text = "\n".join(p for p in text_parts if p)

    return build_raw_item(
        source_kind=SourceKind.CAREER_SITE,
        source_name="Yandex",
        external_id=str(vacancy_id or slug or job_url),
        url=job_url,
        text=text,
        metadata={
            "board_url": base_url,
            "job_url": job_url,
            "service": service_name,
            "service_group": service_group,
            "cities": cities,
            "skills": skills,
            "work_modes": work_modes,
            "parser": "site_yandex_jobs",
        },
    )


@register_site_parser("yandex_jobs", domain_pattern=r"yandex\.ru/jobs")
class YandexJobsParser:
    domain_pattern = r"yandex\.ru/jobs"

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        from playwright.async_api import async_playwright

        from job_ftch.config import get_settings

        settings = get_settings()
        headless = spec.monitor_config.get("headless", True)
        stealth = spec.monitor_config.get("stealth", True)
        limit = spec.limit or settings.career_site_default_limit

        collected: list[dict[str, Any]] = []
        seen_ids: set[int] = set()

        async with async_playwright() as pw:
            launch_args = []
            if stealth:
                launch_args.append("--disable-blink-features=AutomationControlled")
            browser = await pw.chromium.launch(headless=bool(headless), args=launch_args)
            context = await browser.new_context(
                user_agent=spec.monitor_config.get(
                    "user_agent",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                ),
                viewport=spec.monitor_config.get("viewport", {"width": 1440, "height": 900}),
            )
            page = await context.new_page()

            async def _on_response(response: Any) -> None:
                if _API_PATH not in response.url:
                    return
                if "cursor" not in response.url and "is_fast_track" not in response.url:
                    return
                try:
                    body = await response.json()
                except Exception:
                    return
                for item in body.get("results", []):
                    vid = item.get("id")
                    if vid and vid not in seen_ids:
                        seen_ids.add(vid)
                        collected.append(item)

            page.on("response", _on_response)

            try:
                await page.goto(spec.url, wait_until="networkidle", timeout=30000)
                # Scroll to trigger pagination API calls
                last_height = 0
                stale_rounds = 0
                for _ in range(120):
                    current_height = await page.evaluate("() => document.body.scrollHeight")
                    if current_height == last_height:
                        stale_rounds += 1
                        if stale_rounds >= 4:
                            break
                    else:
                        stale_rounds = 0
                    last_height = current_height
                    await page.evaluate("() => window.scrollBy(0, 3000)")
                    await asyncio.sleep(1.2)
            finally:
                await browser.close()

        logger.info(
            "yandex_parser_api_collected",
            url=spec.url,
            api_items=len(collected),
            limit=limit,
        )

        emitted = 0
        for payload in collected[:limit]:
            item = _item_from_api(payload, spec.url)
            if item is None:
                continue
            yield item
            emitted += 1
        logger.info("yandex_parser_emitted", url=spec.url, emitted=emitted)
