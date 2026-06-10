from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import structlog

from job_ftch.application.contracts import Source
from job_ftch.application.registry import (
    detect_monitor_type,
    register_source_v2,
    resolve_monitor,
    resolve_scraper,
)
from job_ftch.infrastructure.sources.site_models import (
    DiscoveredPostingPayload,
    ScrapedPostingPayload,
)
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
    ) -> None:
        self.spec = spec
        self.http = http_client
        self.auth = auth
        self.kind = "career_site"
        self.stats = {
            "monitored": 0,
            "rich_emitted": 0,
            "scraped": 0,
            "scrape_fallback_used": 0,
            "source_partial": False,
            "monitor_truncated": 0,
        }

    async def fetch(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        monitor_name = self.spec.monitor
        monitor_config = self.spec.monitor_config

        # 1. Resolve monitor
        if monitor_name == "auto" or not monitor_name:
            detected = await detect_monitor_type(self.spec.url, self.http)
            if detected:
                monitor_name, auto_config = detected
                monitor_config = {**auto_config, **monitor_config}
                logger.info("auto_detected_monitor", name=monitor_name, url=self.spec.url)
            else:
                monitor_name = "sitemap"  # Fallback to sitemap discovery
                logger.info("monitor_auto_detect_failed_falling_back_to_sitemap", url=self.spec.url)

        try:
            monitor_entry = resolve_monitor(monitor_name)
        except ValueError:
            logger.error("unsupported_monitor", name=monitor_name)
            return

        # 2. Run monitor
        try:
            monitor_instance = monitor_entry.factory(self.spec, self.http, self.auth)
            if hasattr(monitor_instance, "discover"):
                raw_result = await monitor_instance.discover(self.spec, self.http)
            else:
                # Factory might return a coroutine directly
                raw_result = await monitor_instance

            result = normalize_monitor_result(raw_result)
        except Exception as exc:
            logger.exception("monitor_run_failed", name=monitor_name, error=str(exc))
            return

        # 3. Apply filters/transforms
        result = apply_url_filter(result, self.spec.url_filter)
        result = apply_url_transform(result, self.spec.url_transform)

        self.stats["monitored"] = len(result.urls)
        self.stats["source_partial"] = result.truncated
        if result.truncated:
            self.stats["monitor_truncated"] = 1

        # 4. Handle rich payloads (no scraper needed)
        emitted_urls = set()
        if result.payloads_by_url:
            for url, payload in result.payloads_by_url.items():
                if url not in result.urls:
                    continue  # Filtered out
                yield payload_to_raw_item(payload, self.spec, self.spec.source_name or monitor_name)
                emitted_urls.add(url)
                self.stats["rich_emitted"] += 1

        # 5. Handle URL-only discovery (needs scraper)
        urls_to_scrape = result.urls - emitted_urls
        if not urls_to_scrape:
            return

        scraper_chain = self._resolve_scraper_chain(monitor_name, monitor_config)
        
        limit = self.spec.detail_limit or self.spec.limit
        count = 0
        for url in urls_to_scrape:
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
                yield payload_to_raw_item(full_payload, self.spec, self.spec.source_name or monitor_name)
                self.stats["scraped"] += 1
                count += 1

        logger.info("career_site_fetch_complete", **self.stats)

    def _resolve_scraper_chain(self, monitor_name: str, monitor_config: dict[str, Any]) -> list[str]:
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

    async def _scrape_with_fallback(self, url: str, scraper_chain: list[str]) -> ScrapedPostingPayload | None:
        """Try scrapers in order until one returns a result with content."""
        for i, scraper_name in enumerate(scraper_chain):
            try:
                scraper_entry = resolve_scraper(scraper_name)
                payload = await scraper_entry.factory(url, self.spec.scraper_config, self.http)

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


@register_source_v2("career_site")
def _build_career_site_source(
    spec: CareerSiteSpec,
    auth: AuthProvider,
) -> CareerSiteSource:
    from job_ftch.infrastructure.sources.career_site import build_default_http_client

    return CareerSiteSource(
        spec=spec,
        http_client=build_default_http_client(),
        auth=auth,
    )
