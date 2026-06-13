from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

import httpx
import structlog

from job_ftch.application.contracts import Source
from job_ftch.application.registry import (
    register_source,
    register_source_spec,
    resolve_bypass,
    resolve_monitor,
    resolve_scraper,
)
from job_ftch.domain.site_models import (
    DiscoveredPostingPayload,
    ScrapedPostingPayload,
)
from job_ftch.infrastructure.sources.career_site import client_for_config
from job_ftch.infrastructure.sources.monitor_detector import detect_monitor_type
from job_ftch.infrastructure.sources.site_utils import (
    apply_url_filter,
    apply_url_transform,
    normalize_monitor_result,
    payload_to_raw_item,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.application.contracts import AuthProvider
    from job_ftch.domain import QuarantinedRawItem, RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

logger = structlog.get_logger("job_ftch.sources.career_site")


class CareerSiteSource(Source["RawItem"]):
    """Orchestrates monitors and scrapers to fetch jobs from career sites."""

    def __init__(
        self,
        spec: CareerSiteSpec,
        http_client: Any,
        auth: AuthProvider,
        store: Any = None,
    ) -> None:
        self.spec = spec
        self.http = http_client
        self.auth = auth
        self.store = store
        self.kind = "career_site"
        self.bypass_strategy: Any = None
        self.stats = {
            "monitored": 0,
            "rich_emitted": 0,
            "scraped": 0,
            "scrape_fallback_used": 0,
            "source_partial": False,
            "monitor_truncated": 0,
        }

    def _try_escalate_bypass(self, exc: Exception) -> bool:
        """Attempt to escalate bypass strategy if blocked. Returns True if escalated."""
        is_blocked = False
        if (
            isinstance(exc, httpx.HTTPStatusError)
            and exc.response.status_code in (403, 401, 429, 503)
        ) or isinstance(exc, httpx.TimeoutException):
            is_blocked = True
        else:
            err_str = str(exc).lower()
            if (
                "timeout" in err_str
                or "navigation failed" in err_str
                or "err_aborted" in err_str
                or "blocked" in err_str
            ):
                is_blocked = True

        if (
            is_blocked
            and hasattr(self.bypass_strategy, "escalate")
            and self.bypass_strategy.escalate()
        ):
            logger.warning(
                "monitor_blocked_escalating",
                error=str(exc),
                next_tier=self.bypass_strategy.current_name,
            )
            return True
        return False

    async def fetch(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        from urllib.parse import urlparse

        domain = urlparse(self.spec.url).netloc
        cached_strategy = None
        if self.store and (
            not self.spec.bypass
            or self.spec.bypass == "auto"
            or not self.spec.monitor
            or self.spec.monitor == "auto"
        ):
            cached_strategy = await self.store.get_source_strategy(domain)
            if cached_strategy:
                logger.info(
                    "using_cached_strategy",
                    domain=domain,
                    monitor=cached_strategy.get("monitor"),
                    bypass=cached_strategy.get("bypass"),
                )

        initial_bypass = self.spec.bypass or (
            cached_strategy.get("bypass") if cached_strategy else "auto"
        )
        self.bypass_strategy = resolve_bypass(initial_bypass, self.spec.bypass_config)
        original_http = self.http

        initial_monitor_name = self.spec.monitor or (
            cached_strategy.get("monitor") if cached_strategy else "auto"
        )
        monitor_config = self.spec.monitor_config

        # 1. Resolve monitors to try
        monitors_to_try = []
        if initial_monitor_name == "auto":
            # get_ordered_monitors already performs fingerprinting and returns the best monitor list.
            # No separate detect_monitor_type call needed here to avoid extra HTTP requests.
            from job_ftch.infrastructure.sources.monitor_detector import get_ordered_monitors
            async with client_for_config(self.http, monitor_config) as _fp_client:
                try:
                    monitors_to_try = await get_ordered_monitors(self.spec.url, _fp_client)
                except Exception:
                    monitors_to_try = ["dom", "api_sniffer"]
            
            # Ensure dom and api_sniffer always present as ultimate fallbacks
            for fallback in ["dom", "api_sniffer"]:
                if fallback not in monitors_to_try:
                    monitors_to_try.append(fallback)
        else:
            monitors_to_try = [initial_monitor_name]

        for current_monitor_name in monitors_to_try:
            logger.info("trying_monitor", name=current_monitor_name, url=self.spec.url)

            while True:
                self.http = await self.bypass_strategy.apply_http(original_http)

                try:
                    monitor_entry = resolve_monitor(current_monitor_name)
                except ValueError:
                    logger.error("unsupported_monitor", name=current_monitor_name)
                    break  # Try next monitor in chain

                # 2. Run monitor
                try:
                    async with client_for_config(self.http, monitor_config) as monitor_http:
                        monitor_instance = monitor_entry.factory(self.spec, monitor_http, self.auth)
                        if hasattr(monitor_instance, "discover"):
                            raw_result = await monitor_instance.discover(self.spec, monitor_http)
                        else:
                            raw_result = await monitor_instance

                    result = normalize_monitor_result(raw_result)
                except Exception as exc:
                    if self._try_escalate_bypass(exc):
                        continue
                    logger.warning("monitor_run_failed", name=current_monitor_name, error=str(exc))
                    break  # Try next monitor in chain

                # 3. Apply filters/transforms EARLY to check if we found anything useful
                result = apply_url_filter(result, self.spec.url_filter)
                result = apply_url_transform(result, self.spec.url_transform)

                # 4. Check if we found anything useful. If not, and we are in auto mode, try next monitor.
                # We ignore the self-url fallback for this check.
                found_something = bool(result.payloads_by_url)
                if not found_something:
                    other_urls = {
                        u for u in result.urls if u.rstrip("/") != self.spec.url.rstrip("/")
                    }
                    found_something = bool(other_urls)

                # If url_filter was set but matched nothing, the page is likely JS-rendered.
                # Escalate bypass (noop → curl_stealth → stealth_browser) so a real browser
                # executes the JavaScript and exposes the job links.
                # url_filter may live in monitor_config (YAML nesting) rather than spec.url_filter.
                _has_url_filter = bool(
                    self.spec.url_filter or self.spec.monitor_config.get("url_filter")
                )
                if (
                    not found_something
                    and _has_url_filter
                    and hasattr(self.bypass_strategy, "escalate")
                    and self.bypass_strategy.escalate()
                ):
                    logger.warning(
                        "monitor_empty_escalating",
                        reason="url_filter_matched_nothing",
                        next_tier=self.bypass_strategy.current_name,
                    )
                    continue  # retry same monitor with escalated bypass (e.g. stealth_browser)

                if (
                    not found_something
                    and initial_monitor_name == "auto"
                    and current_monitor_name != monitors_to_try[-1]
                ):
                    logger.info(
                        "monitor_yielded_no_useful_results_trying_next", name=current_monitor_name
                    )
                    break  # break inner while True, move to next monitor in outer for

                # If we got here, we either have results or we exhausted bypasses for this monitor
                # and still have no results but it's the last monitor, or we found something!

                self.stats["monitored"] = len(result.urls)
                self.stats["source_partial"] = result.truncated
                if result.truncated:
                    self.stats["monitor_truncated"] = 1

                # 5. Handle rich payloads (no scraper needed)
                emitted_urls = set()
                if result.payloads_by_url:
                    for url, payload in result.payloads_by_url.items():
                        if url not in result.urls:
                            continue  # Filtered out
                        yield payload_to_raw_item(
                            payload, self.spec, self.spec.source_name or current_monitor_name
                        )
                        emitted_urls.add(url)
                        self.stats["rich_emitted"] += 1

                # 6. Handle URL-only discovery (needs scraper)
                urls_to_scrape = result.urls - emitted_urls
                if not urls_to_scrape and self._should_scrape_source_url():
                    urls_to_scrape = {self.spec.url}

                if urls_to_scrape:
                    scraper_chain = self._resolve_scraper_chain(
                        current_monitor_name, monitor_config
                    )

                    limit = self.spec.detail_limit or self.spec.limit
                    for count, url in enumerate(urls_to_scrape):
                        if count >= limit:
                            self.stats["truncated"] = True
                            break

                        scrape_result = await self._scrape_with_fallback(url, scraper_chain)
                        if scrape_result:
                            full_payload = DiscoveredPostingPayload(
                                url=url,
                                title=scrape_result.title,
                                description=scrape_result.description,
                                locations=scrape_result.locations,
                                employment_type=scrape_result.employment_type,
                                job_location_type=scrape_result.job_location_type,
                                date_posted=scrape_result.date_posted,
                                base_salary=scrape_result.base_salary,
                                language=scrape_result.language,
                                extras=scrape_result.extras,
                                metadata=scrape_result.metadata,
                            )
                            yield payload_to_raw_item(
                                full_payload,
                                self.spec,
                                self.spec.source_name or current_monitor_name,
                            )
                            self.stats["scraped"] += 1

                if self.store and (
                    not self.spec.bypass
                    or self.spec.bypass == "auto"
                    or not self.spec.monitor
                    or self.spec.monitor == "auto"
                ):
                    await self.store.save_source_strategy(
                        domain,
                        current_monitor_name,
                        getattr(
                            self.bypass_strategy,
                            "current_name",
                            self.spec.bypass or initial_bypass or "noop",
                        ),
                    )

                logger.info("career_site_fetch_complete", **self.stats)
                return  # Successfully finished with one monitor

        logger.info("career_site_fetch_exhausted_all_monitors", **self.stats)

    def _resolve_scraper_chain(
        self, monitor_name: str, monitor_config: dict[str, Any]
    ) -> list[str]:
        """Determine primary and fallback scrapers based on monitor type."""
        if self.spec.scraper:
            chain = [self.spec.scraper]
            chain.extend(self.spec.scraper_fallback)
            return chain

        # Auto-resolve from monitor name (Phase 7)
        if monitor_name == "sitemap":
            return ["json-ld", "embedded", "nextdata", "dom"]
        if monitor_name == "dom":
            return ["json-ld", "embedded", "dom"]
        if monitor_name == "nextdata":
            return ["nextdata", "json-ld", "embedded"]
        if monitor_name == "workday":
            return ["workday", "json-ld"]
        if monitor_name == "smartrecruiters":
            return ["smartrecruiters", "json-ld"]

        # Default chain
        return ["json-ld", "embedded", "nextdata", "dom"]

    def _should_scrape_source_url(self) -> bool:
        include_self = self.spec.monitor_config.get("include_self_url", False)
        if include_self:
            return True

        lowered = self.spec.url.lower()
        detail_markers = ("/job/", "/jobs/", "/vacancy/", "/vacancies/")
        return any(marker in lowered for marker in detail_markers)

    def _should_render_detail(self) -> bool:
        return bool(
            self.spec.scraper_config.get("render") or self.spec.monitor_config.get("render")
        )

    async def _fetch_detail_html_with_browser(self, url: str) -> str | None:
        try:
            from playwright.async_api import async_playwright

            from job_ftch.infrastructure.sources.browser_utils import (
                BROWSER_KEYS,
                navigate,
                open_page,
            )
        except ImportError:
            return None

        browser_config = {
            **{k: v for k, v in self.spec.monitor_config.items() if k in BROWSER_KEYS},
            **{k: v for k, v in self.spec.scraper_config.items() if k in BROWSER_KEYS},
        }

        try:
            async with (
                async_playwright() as pw,
                open_page(pw, browser_config, bypass_strategy=self.bypass_strategy) as page,
            ):
                await navigate(page, url, browser_config)
                settle_seconds = float(
                    self.spec.scraper_config.get(
                        "settle_seconds",
                        self.spec.monitor_config.get("settle_seconds", 0),
                    )
                )
                if settle_seconds > 0:
                    await asyncio.sleep(settle_seconds)
                return await page.content()
        except Exception as exc:
            logger.debug("detail_browser_prefetch_failed", url=url, error=str(exc))
            return None

    async def _scrape_with_fallback(
        self, url: str, scraper_chain: list[str]
    ) -> ScrapedPostingPayload | None:
        """Try scrapers in order until one returns a result with content."""
        prefetched_html: str | None = None
        async with client_for_config(self.http, self.spec.scraper_config) as scrape_http:
            try:
                response = await scrape_http.get(url, follow_redirects=True)
                prefetched_html = getattr(response, "text", None)
            except Exception:
                prefetched_html = None

            if not prefetched_html and self._should_render_detail():
                prefetched_html = await self._fetch_detail_html_with_browser(url)

            for i, scraper_name in enumerate(scraper_chain):
                try:
                    scraper_entry = resolve_scraper(scraper_name)
                    scraper_config = dict(self.spec.scraper_config or {})
                    if (
                        prefetched_html
                        and not scraper_config
                        and scraper_entry.can_handle is not None
                    ):
                        inferred = scraper_entry.can_handle([prefetched_html])
                        if isinstance(inferred, dict):
                            scraper_config = inferred
                    if prefetched_html:
                        scraper_config["prefetched_html"] = prefetched_html

                    payload = await scraper_entry.factory(url, scraper_config, scrape_http)

                    typed_payload = cast("ScrapedPostingPayload | None", payload)
                    if typed_payload and (typed_payload.title or typed_payload.description):
                        if i > 0:
                            self.stats["scrape_fallback_used"] += 1
                        return typed_payload
                except Exception as exc:
                    logger.debug("scraper_failed", name=scraper_name, url=url, error=str(exc))
                    continue

        logger.warning("all_scrapers_failed", url=url, chain=scraper_chain)
        return None


@register_source_spec("career_site")
def _build_career_site_source(
    spec: CareerSiteSpec,
    auth: AuthProvider,
    store: Any = None,
) -> CareerSiteSource:
    from job_ftch.infrastructure.sources.career_site import build_default_http_client

    return CareerSiteSource(
        spec=spec,
        http_client=build_default_http_client(),
        auth=auth,
        store=store,
    )


@register_source("career_site")
def _career_site_factory(settings: Settings) -> object:
    from job_ftch.application.registry import create_source_from_spec
    from job_ftch.domain.source_spec import CareerSiteSpec

    if settings.career_site_url is None:
        msg = "Career site source requires JOB_FTCH_CAREER_SITE_URL."
        raise ValueError(msg)

    return create_source_from_spec(
        CareerSiteSpec(
            type="career_site",
            url=settings.career_site_url,
            limit=settings.pipeline_max_items_per_run,
        )
    )
