from __future__ import annotations

import asyncio
import contextlib
import hashlib
import html as html_lib
import inspect
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
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
from job_ftch.domain.ingest_models import (
    DiscoveredCandidate,
    IngestItemStatus,
    score_completeness,
)
from job_ftch.domain.site_models import (
    DiscoveredPostingPayload,
    ScrapedPostingPayload,
)
from job_ftch.infrastructure.sources.career_detail_runner import run_scraper_attempt
from job_ftch.infrastructure.sources.career_monitor_runner import run_monitor_attempt
from job_ftch.infrastructure.sources.career_site import client_for_config
from job_ftch.infrastructure.sources.shared_limiters import detail_slot
from job_ftch.infrastructure.sources.site_defaults import apply_runtime_defaults
from job_ftch.infrastructure.sources.site_utils import (
    apply_url_filter,
    apply_url_transform,
    payload_to_raw_item,
)
from job_ftch.infrastructure.sources.source_deadline import (
    await_with_source_deadline,
    sleep_with_source_deadline,
)
from job_ftch.infrastructure.sources.url_scoring import (
    is_probable_job_url,
    is_same_site_family,
    rank_job_urls,
    score_job_url,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.application.contracts import AuthProvider
    from job_ftch.config import Settings
    from job_ftch.domain import QuarantinedRawItem, RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

logger = structlog.get_logger("job_ftch.sources.career_site")

# Cap on concurrent store has_processed() checks during pre-dedup. A monitor may
# return thousands of URLs (dom cap is 10k); an unbounded gather would open that
# many simultaneous store operations and can exhaust a real Redis/SQL pool.
_PRE_DEDUP_MAX_CONCURRENCY = 64
# A generic monitor can expose tens of thousands of links.  Detail extraction
# only needs a small ranked frontier; materialising every candidate before the
# limit is applied can consume the entire source budget.
_GENERIC_CANDIDATE_MULTIPLIER = 20
_GENERIC_CANDIDATE_FLOOR = 100
# Sitemap is an unstructured site inventory.  A weak positive score is often
# a product, location, or research page that merely happens to contain a
# career-related token; do not spend the source deadline fetching it.
_SITEMAP_DETAIL_SCORE_MINIMUM = 5
_ARTICLE_OPEN_GRAPH_RE = re.compile(
    r"<meta\b[^>]+(?:property|name)=[\"']og:type[\"'][^>]+content=[\"']article[\"']"
    r"|<meta\b[^>]+content=[\"']article[\"'][^>]+(?:property|name)=[\"']og:type[\"']",
    re.IGNORECASE,
)


class ZeroYieldReason(StrEnum):
    CONFIRMED_EMPTY = "confirmed_empty"
    ALL_MONITORS_EXHAUSTED = "all_monitors_exhausted"
    MONITOR_EMPTY = "monitor_empty"
    BLOCKED_NO_BYPASS_LEFT = "blocked_no_bypass_left"
    ALL_SCRAPERS_FAILED = "all_scrapers_failed"
    SOFT_404 = "soft_404"
    FRESHNESS_FILTERED = "freshness_filtered"
    PRE_DEDUP_ALL_SEEN = "pre_dedup_all_seen"
    BOARD_GONE = "board_gone"


@dataclass
class FetchStats:
    monitored: int = 0
    detail_attempted: int = 0
    rich_emitted: int = 0
    scraped: int = 0
    scrape_fallback_used: int = 0
    browser_navigations_attempted: int = 0
    source_partial: bool = False
    monitor_truncated: int = 0
    freshness_filtered: int = 0
    freshness_undated_passed: int = 0
    parser_duplicates_suppressed: int = 0
    detail_protection_failures: int = 0
    protection_circuit_open: bool = False
    truncated: bool = False
    recommended_monitors: list[str] = field(default_factory=list)
    monitor_attempts: list[str] = field(default_factory=list)
    final_monitor: str | None = None
    successful_scraper: str | None = None
    detected_monitor_config: dict[str, Any] = field(default_factory=dict)
    zero_reason: ZeroYieldReason | None = None

    def to_log_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "monitored": self.monitored,
            "detail_attempted": self.detail_attempted,
            "rich_emitted": self.rich_emitted,
            "scraped": self.scraped,
            "scrape_fallback_used": self.scrape_fallback_used,
            "source_partial": self.source_partial,
            "monitor_truncated": self.monitor_truncated,
            "freshness_filtered": self.freshness_filtered,
            "freshness_undated_passed": self.freshness_undated_passed,
            "parser_duplicates_suppressed": self.parser_duplicates_suppressed,
            "detail_protection_failures": self.detail_protection_failures,
            "protection_circuit_open": self.protection_circuit_open,
            "truncated": self.truncated,
            "recommended_monitors": self.recommended_monitors,
            "monitor_attempts": self.monitor_attempts,
            "final_monitor": self.final_monitor,
            "successful_scraper": self.successful_scraper,
            "detected_monitor_config": self.detected_monitor_config,
        }
        if self.zero_reason is not None:
            d["zero_reason"] = self.zero_reason.value
        return d


def _parse_retry_after(raw_value: str | None) -> float | None:
    from job_ftch.infrastructure.sources.http_retry import parse_retry_after

    return parse_retry_after(raw_value)


def _should_enable_render_on_monitor_retry(strategy: Any) -> bool:
    """Use declared capability rather than a central backend-name list."""
    return bool(getattr(strategy, "requires_browser", False))


def _should_escalate_empty_monitor(*, has_url_filter: bool, monitor_suggests_spa: bool) -> bool:
    """Return whether an empty monitor result is evidence for a render retry.

    A normal empty DOM extraction is not proof that a site is client-rendered.
    Escalating every final empty monitor to a browser route starves unrelated
    sources of the shared deadline budget. Only explicit source configuration
    can justify the expensive retry.
    """
    return has_url_filter or monitor_suggests_spa


def _is_protected_source_error(exc: BaseException) -> bool:
    """Whether an exception chain contains an access-control response."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        status = getattr(getattr(current, "response", None), "status_code", None)
        if status in {401, 403, 429}:
            return True
        current = current.__cause__ or current.__context__
    return False


def _looks_like_spa_shell(html: str | None) -> bool:
    if not html:
        return False
    lowered = html.lower()
    if "jobposting" in lowered:
        return False
    if "__next_data__" in lowered or "__nuxt__" in lowered:
        return True
    if '<div id="root"' in lowered or "<div id='root'" in lowered:
        return True
    if '<div id="app"' in lowered or "<div id='app'" in lowered:
        return True
    body_match = re.search(r"<body\b[^>]*>(.*?)</body>", lowered, re.DOTALL)
    body = body_match.group(1) if body_match else lowered
    visible_text = re.sub(r"<[^>]+>", " ", body)
    normalized = " ".join(visible_text.split())
    # A short but semantically populated detail page is not an SPA shell.
    # The previous length-only rule caused valid static fixtures (and small
    # real vacancy pages) to be replaced by an unrelated browser response.
    has_semantic_heading = bool(re.search(r"<(?:h1|title)\b", lowered))
    return len(normalized) < 80 and not has_semantic_heading


def _visible_description_fallback(html: str | None) -> str | None:
    """Recover a small static body when structured scrapers found only a title."""
    if not html:
        return None
    body_match = re.search(r"<body\b[^>]*>(.*?)</body>", html, re.IGNORECASE | re.DOTALL)
    body = body_match.group(1) if body_match else html
    body = re.sub(
        r"<\s*(?:script|style|noscript|svg)\b[^>]*>.*?</\s*(?:script|style|noscript|svg)\s*>",
        " ",
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    body = re.sub(
        r"<\s*(?:h1|title)\b[^>]*>.*?</\s*(?:h1|title)\s*>",
        " ",
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    visible = html_lib.unescape(re.sub(r"<[^>]+>", " ", body))
    normalized = " ".join(visible.split())
    return normalized if len(normalized) >= 30 else None


def _declares_editorial_article(html: str | None) -> bool:
    """Whether page metadata explicitly classifies the document as editorial."""
    return bool(html and _ARTICLE_OPEN_GRAPH_RE.search(html))


# Matches a URL that points at a SPECIFIC job posting: a job/vacancy marker
# segment followed by an id-bearing segment (contains a digit, e.g. a numeric id
# or a "84179-senior-engineer" slug). Listing/search entry points such as
# /search/vacancy, /vacancies, /jobs/, /job/openings, /vakansii/ do NOT match,
# because their segment after the marker is empty or a generic word with no id.
_DETAIL_URL_RE = re.compile(
    r"/(?:job|jobs|vacancy|vacancies|vakansii|position|positions|posting|postings"
    r"|work|project|projects"
    r"|locuri-de-munca|locuri_de_munca"
    r"|career|careers"
    r"|opening|openings"
    r"|offer|offers"
    r"|ployment|ployments"
    r"|stelle|stellen"
    r"|offre|offres"
    r"|empleo|empleos"
    r"|trabajo|trabajos)"
    r"/[^/?#]*\d[^/?#]*(?:\.(?:html?|php|aspx))?",
    re.IGNORECASE,
)


def _rank_detail_urls(urls: list[str] | set[str], board_url: str) -> list[str]:
    ranked = rank_job_urls(urls, board_url=board_url)
    if not ranked:
        return []
    if board_url in ranked and _DETAIL_URL_RE.search(board_url):
        ranked = [board_url, *[url for url in ranked if url != board_url]]
    scored = [(url, score_job_url(url, board_url=board_url)) for url in ranked]
    positives = [url for url, score in scored if score > 0]
    if positives:
        if board_url in ranked and board_url not in positives and _DETAIL_URL_RE.search(board_url):
            return [board_url, *positives]
        return positives
    return ranked


def _is_valid_detail_candidate(url: str, board_url: str) -> bool:
    from urllib.parse import urlsplit, urlunsplit

    is_ats = any(
        ats in url
        for ats in (
            "jobs.smartrecruiters.com",
            "lever.co",
            "greenhouse.io",
            "workday.com",
            "myworkdayjobs.com",
            "breezy.hr",
            "workable.com",
            "teamtailor.com",
        )
    )
    if not is_same_site_family(url, board_url=board_url) and not is_ats:
        return False
    if is_ats:
        return True
    candidate_parts = urlsplit(url)
    board_parts = urlsplit(board_url)
    candidate_document = (
        urlunsplit((candidate_parts.scheme, candidate_parts.netloc, candidate_parts.path, "", ""))
        .rstrip("/")
        .casefold()
    )
    board_document = (
        urlunsplit((board_parts.scheme, board_parts.netloc, board_parts.path, "", ""))
        .rstrip("/")
        .casefold()
    )
    if candidate_document == board_document:
        return bool(_DETAIL_URL_RE.search(board_url))
    # DOM and sitemap monitors can surface arbitrary same-site links.  Keep
    # ATS locators above untouched, but require a strong detail signal before
    # allocating the shared detail budget to a generic URL (e.g. reject
    # /andersen-offices/poland-krakow discovered from a careers page).
    return (
        is_probable_job_url(url, board_url=board_url)
        and score_job_url(url, board_url=board_url) >= _SITEMAP_DETAIL_SCORE_MINIMUM
    )


def _is_filtered_listing_url(url: str) -> bool:
    from urllib.parse import parse_qsl, urlparse

    query_keys = {key.casefold() for key, _value in parse_qsl(urlparse(url).query)}
    return bool(query_keys & {"q", "query", "text", "keywords", "search", "filter", "filters"})


def _parse_source_datetime(
    value: str | None,
    *,
    relative_base: datetime | None = None,
) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        for fmt in (
            "%Y-%m-%d",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%a, %d %b %Y %H:%M:%S %z",
        ):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            try:
                import dateparser

                base = relative_base or datetime.now(UTC)
                parsed = dateparser.parse(
                    value,
                    settings={
                        "RELATIVE_BASE": base,
                        "RETURN_AS_TIMEZONE_AWARE": True,
                        "TIMEZONE": "UTC",
                        "TO_TIMEZONE": "UTC",
                        "PREFER_DATES_FROM": "past",
                    },
                )
            except (ImportError, TypeError, ValueError, OverflowError):
                return None
            if parsed is None:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _raw_item_posted_at(item: object) -> str | None:
    created_at = getattr(item, "created_at", None)
    if isinstance(created_at, datetime):
        return created_at.isoformat()
    metadata = getattr(item, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    for key in ("date_posted", "posted_at", "published_at", "created_at"):
        value = metadata.get(key)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


class CareerSiteSource(Source["RawItem"]):
    """Orchestrates monitors and scrapers to fetch jobs from career sites."""

    def __init__(
        self,
        spec: CareerSiteSpec,
        http_client: Any,
        auth: AuthProvider,
        store: Any = None,
        *,
        own_http_client: bool = False,
    ) -> None:
        self.spec = apply_runtime_defaults(spec)
        self.http = http_client
        self._base_http = http_client
        self._own_http_client = own_http_client
        self._temporary_http_clients: list[Any] = []
        self.auth = auth
        self.store = store
        self.kind = "career_site"
        self.bypass_strategy: Any = None
        self._bypass_ctx: Any = None
        self.stats = FetchStats()
        self._trusted_parser_urls: set[str] = set()
        self._parser_failure_is_terminal = False
        self._detail_protection_circuit_open = False

    async def _apply_bypass_http(self, original_http: Any) -> Any:
        """Apply bypass and remember client instances created for this run.

        Bypass tiers may create independent httpx/curl sessions.  They are not
        owned by the caller and otherwise survive every retry until process
        exit, exhausting sockets in long batch runs.
        """
        active_http = await self.bypass_strategy.apply_http(original_http)
        self._remember_temporary_http_client(active_http, original_http)
        return active_http

    def _remember_temporary_http_client(self, client: Any, parent: Any) -> None:
        if client is parent:
            return
        if not any(client is known_client for known_client in self._temporary_http_clients):
            self._temporary_http_clients.append(client)

    async def _close_http_client(self, client: Any) -> None:
        close = getattr(client, "aclose", None)
        try:
            if callable(close):
                result = close()
                if inspect.isawaitable(result):
                    await result
                return
            exit_ = getattr(client, "__aexit__", None)
            if callable(exit_):
                result = exit_(None, None, None)
                if inspect.isawaitable(result):
                    await result
        except Exception as exc:  # noqa: BLE001
            logger.warning("career_site_http_cleanup_failed", error=str(exc))

    async def _close_owned_http_clients(self) -> None:
        for client in reversed(self._temporary_http_clients):
            await self._close_http_client(client)
        self._temporary_http_clients.clear()
        if self._own_http_client:
            await self._close_http_client(self._base_http)

    def _runtime_monitor_spec(
        self, monitor_config_override: dict[str, Any] | None = None
    ) -> CareerSiteSpec:
        monitor_config = dict(monitor_config_override or self.spec.monitor_config)
        monitor_config["_bypass_strategy"] = self.bypass_strategy
        if self._bypass_ctx is not None:
            monitor_config["_bypass_context"] = self._bypass_ctx
        return self.spec.model_copy(update={"monitor_config": monitor_config})

    def _monitor_config_for_current_retry(self, monitor_config: dict[str, Any]) -> dict[str, Any]:
        retry_config = dict(monitor_config)
        if _should_enable_render_on_monitor_retry(self.bypass_strategy):
            retry_config["render"] = True
        return retry_config

    def _request_bypass_capability(self, action: str, *, reason: str) -> bool:
        """Ask the route controller for a capability, preserving legacy adapters."""
        request = getattr(self.bypass_strategy, "request_capability", None)
        if callable(request):
            return bool(request(action, reason=reason))
        escalate = getattr(self.bypass_strategy, "escalate", None)
        return bool(escalate()) if callable(escalate) else False

    async def _try_escalate_bypass(
        self,
        exc: Exception,
        *,
        response: httpx.Response | None = None,
        response_body: bytes | None = None,
    ) -> bool:
        """Attempt to escalate bypass strategy. Returns True if escalated.

        Per ADR-037, the actual classification goes through
        `bypass_strategy.handle_failure()` (which uses the
        FailureSignal classifier and the per-source failure window).
        Anything that does not implement `handle_failure` falls back to
        the new heuristic: blocked if status is 401/403/429/503 or
        the error string contains a blocked/timeout marker.
        """
        if hasattr(self.bypass_strategy, "handle_failure"):
            previous_tier = getattr(self.bypass_strategy, "current_name", None)
            previous_route = getattr(self.bypass_strategy, "route_state", None)
            status_code = response.status_code if response is not None else None
            try:
                retry_after = None
                if response is not None:
                    retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                try:
                    failure_result = self.bypass_strategy.handle_failure(
                        self.spec.url,
                        status_code=status_code,
                        headers=dict(response.headers) if response is not None else None,
                        body=response_body,
                        error=exc,
                        retry_after=retry_after,
                    )
                except TypeError:
                    failure_result = self.bypass_strategy.handle_failure(
                        self.spec.url,
                        status_code=status_code,
                        body=response_body,
                        error=exc,
                    )
                failure_kind = (
                    await failure_result if inspect.isawaitable(failure_result) else failure_result
                )
            except Exception as inner:  # noqa: BLE001
                logger.warning(
                    "handle_failure_in_bypass_raised",
                    error=str(inner),
                )
                return False
            if self._bypass_ctx and failure_kind:
                self._bypass_ctx.record_failure(self.spec.url, failure_kind)
            current_tier = getattr(self.bypass_strategy, "current_name", None)
            current_route = getattr(self.bypass_strategy, "route_state", None)
            if current_tier != getattr(self, "_last_tier", None):
                object.__setattr__(self, "_last_tier", current_tier)
                logger.warning(
                    "bypass_tier_changed",
                    url=self.spec.url,
                    tier=current_tier,
                )
            if current_tier != previous_tier or current_route != previous_route:
                logger.info(
                    "monitor_failure_route_changed",
                    url=self.spec.url,
                    previous_tier=previous_tier,
                    current_tier=current_tier,
                    failure_kind=failure_kind,
                )
                return True
            logger.info(
                "monitor_failure_without_escalation",
                url=self.spec.url,
                tier=current_tier,
                failure_kind=failure_kind,
            )
            return False

        from job_ftch.infrastructure.bypass.failure_signal import (
            FailureKind,
            classify_error,
        )

        kind = classify_error(exc) if exc is not None else FailureKind.UNKNOWN
        if kind == FailureKind.UNKNOWN and isinstance(exc, httpx.HTTPStatusError):
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (401, 403):
                kind = FailureKind.BLOCKED
            elif status == 429:
                kind = FailureKind.RATE_LIMIT
            elif isinstance(status, int) and status >= 500:
                kind = FailureKind.SERVER_ERROR

        escalatable = kind in {
            FailureKind.BLOCKED,
            FailureKind.BLOCKED_IP,
            FailureKind.BLOCKED_FINGERPRINT,
            FailureKind.TIMEOUT,
            FailureKind.RATE_LIMIT,
            FailureKind.SERVER_ERROR,
            FailureKind.CAPTCHA,
            FailureKind.CHALLENGE,
        }
        if (
            escalatable
            and hasattr(self.bypass_strategy, "escalate")
            and self.bypass_strategy.escalate()
        ):
            logger.warning(
                "monitor_blocked_escalating",
                error=str(exc),
                failure_kind=kind,
                next_tier=self.bypass_strategy.current_name,
            )
            return True
        return False

    async def _init_strategy(self) -> tuple[str, dict[str, Any] | None, Any, str]:
        from urllib.parse import urlparse

        parsed_url = urlparse(self.spec.url)
        domain = parsed_url.netloc
        is_filtered_listing = _is_filtered_listing_url(self.spec.url)

        cached_strategy = None
        if self.store and (
            not self.spec.bypass
            or self.spec.bypass == "auto"
            or not self.spec.monitor
            or self.spec.monitor == "auto"
        ):
            cached_strategy = await self.store.get_source_strategy(domain)
            if cached_strategy:
                if is_filtered_listing:
                    cached_strategy = {
                        **cached_strategy,
                        "monitor": "auto",
                    }
                logger.info(
                    "using_cached_strategy",
                    domain=domain,
                    monitor=cached_strategy.get("monitor"),
                    bypass=cached_strategy.get("bypass"),
                )

        initial_bypass = self.spec.bypass or "auto"
        self.bypass_strategy = resolve_bypass(initial_bypass, self.spec.bypass_config)
        cached_bypass = cached_strategy.get("bypass") if cached_strategy else None
        if (
            initial_bypass == "auto"
            and cached_bypass not in (None, "", "auto", "noop")
            and hasattr(self.bypass_strategy, "escalate_to")
        ):
            with contextlib.suppress(Exception):
                self.bypass_strategy.escalate_to(cached_bypass)
        original_http = self.http

        try:
            from job_ftch.infrastructure.bypass.context import BypassContext

            self._bypass_ctx = await BypassContext.for_url(
                self.spec.url, config=self.spec.bypass_config
            )
            bind_context = getattr(self.bypass_strategy, "bind_context", None)
            if callable(bind_context):
                bind_context(self._bypass_ctx)
        except Exception:
            self._bypass_ctx = None

        return domain, cached_strategy, original_http, initial_bypass

    async def _try_site_parser(
        self, original_http: Any
    ) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        from job_ftch.application.registry import resolve_site_parser

        site_parser = resolve_site_parser(self.spec.url)
        if site_parser is not None and getattr(site_parser, "has_custom_parse", True):
            parser_spec = self._runtime_monitor_spec()
            parser_monitor_config = dict(self.spec.monitor_config)
            supports_discover = getattr(site_parser, "supports_discover", False)
            while True:
                try:
                    self.http = await self._apply_bypass_http(original_http)
                except Exception as exc:
                    logger.warning(
                        "bypass_apply_http_failed",
                        tier=getattr(self.bypass_strategy, "current_name", "?"),
                        error=str(exc),
                    )
                    if await self._try_escalate_bypass(exc):
                        continue
                    break

                attempt_items: list[RawItem | QuarantinedRawItem] = []
                freshness_filtered_before = self.stats.freshness_filtered
                freshness_undated_before = self.stats.freshness_undated_passed
                try:
                    if supports_discover:
                        urls: list[str] = await await_with_source_deadline(
                            site_parser.discover(parser_spec, self.http)
                        )
                        if urls:
                            self._trusted_parser_urls.update(urls)
                            self.stats.monitored = len(urls)
                            candidates = [
                                DiscoveredCandidate(url=u, completeness=0.1) for u in urls
                            ]
                            scraper_chain = self._resolve_scraper_chain(
                                "site_parser",
                                parser_monitor_config,
                            )
                            source_name = self.spec.source_name or type(site_parser).__name__
                            async for enriched_item in self._enrich_candidates(
                                candidates, scraper_chain, source_name
                            ):
                                yield enriched_item
                        elif getattr(site_parser, "confirmed_empty_on_empty", False):
                            self.stats.zero_reason = ZeroYieldReason.CONFIRMED_EMPTY
                            self._parser_failure_is_terminal = True
                        return
                    else:
                        parsed = 0
                        async for parsed_item in site_parser.parse(parser_spec, self.http):
                            parsed += 1
                            if not self._passes_freshness_cutoff(_raw_item_posted_at(parsed_item)):
                                continue
                            attempt_items.append(parsed_item)
                        if attempt_items:
                            unique_items = self._dedupe_parser_items(attempt_items)
                            for unique_item in unique_items:
                                yield unique_item
                            return
                        if parsed and self.stats.freshness_filtered >= parsed:
                            self.stats.zero_reason = ZeroYieldReason.FRESHNESS_FILTERED
                        elif getattr(site_parser, "confirmed_empty_on_empty", False):
                            self.stats.zero_reason = ZeroYieldReason.CONFIRMED_EMPTY
                            # The parser consulted an authoritative listing
                            # API.  Do not let a generic crawl overwrite this
                            # factual empty-board result.
                            self._parser_failure_is_terminal = True
                            return
                        else:
                            self.stats.zero_reason = ZeroYieldReason.MONITOR_EMPTY
                        if getattr(site_parser, "terminal_on_empty", False):
                            # Some registered boards intentionally expose no
                            # public listing data when unauthenticated.  A
                            # generic crawl after their dedicated parser adds
                            # noise and must not be mistaken for an empty site.
                            self.stats.zero_reason = ZeroYieldReason.BLOCKED_NO_BYPASS_LEFT
                            self._parser_failure_is_terminal = True
                            return
                        break  # Parsed but found 0 items, break out of bypass loop
                except Exception as exc:
                    response = (
                        getattr(exc, "response", None)
                        if isinstance(exc, httpx.HTTPStatusError)
                        else None
                    )
                    body = response.content if response is not None else None
                    if await self._try_escalate_bypass(exc, response=response, response_body=body):
                        if not supports_discover:
                            self.stats.freshness_filtered = freshness_filtered_before
                            self.stats.freshness_undated_passed = freshness_undated_before
                        continue
                    if not supports_discover and attempt_items:
                        self.stats.source_partial = True
                        logger.warning(
                            "site_parser_partial_results",
                            url=self.spec.url,
                            yielded=len(attempt_items),
                            error=str(exc),
                        )
                        for partial_item in self._dedupe_parser_items(attempt_items):
                            yield partial_item
                        return
                    if _is_protected_source_error(exc):
                        self.stats.zero_reason = ZeroYieldReason.BLOCKED_NO_BYPASS_LEFT
                        # A known site parser has exhausted its own bypass path.
                        # Falling through to generic discovery only turns a clear
                        # protected-source outcome into an unbounded unrelated crawl.
                        self._parser_failure_is_terminal = True
                    logger.warning(
                        "site_parser_failed_terminal"
                        if self._parser_failure_is_terminal
                        else "site_parser_failed_falling_back",
                        url=self.spec.url,
                        error=str(exc),
                    )
                    break

    def _dedupe_parser_items(
        self, items: list[RawItem | QuarantinedRawItem]
    ) -> list[RawItem | QuarantinedRawItem]:
        unique: list[RawItem | QuarantinedRawItem] = []
        seen: set[str] = set()
        for item in items:
            key = str(getattr(item, "stable_id", "") or getattr(item, "url", "") or "")
            if key and key in seen:
                self.stats.parser_duplicates_suppressed += 1
                continue
            if key:
                seen.add(key)
            unique.append(item)
        if len(unique) != len(items):
            logger.info(
                "site_parser_duplicates_suppressed",
                url=self.spec.url,
                suppressed=len(items) - len(unique),
            )
        return unique

    async def _resolve_monitors(
        self,
        original_http: Any,
        cached_strategy: dict[str, Any] | None,
        monitor_config: dict[str, Any],
    ) -> tuple[list[str], dict[str, Any]]:
        initial_monitor_name = self.spec.monitor or (
            cached_strategy.get("monitor") if cached_strategy else "auto"
        )
        resolved_initial_monitor_name = str(initial_monitor_name or "auto")

        monitors_to_try: list[str] = []
        if resolved_initial_monitor_name == "auto":
            from job_ftch.infrastructure.sources.monitor_detector import get_ordered_monitors

            try:
                _fp_http = await self._apply_bypass_http(original_http)
            except Exception:
                _fp_http = original_http
            async with client_for_config(_fp_http, monitor_config) as _fp_client:
                try:
                    (
                        monitors_to_try,
                        detected_monitor_config,
                        canonical_url,
                    ) = await get_ordered_monitors(self.spec.url, _fp_client)
                    if canonical_url and canonical_url != self.spec.url:
                        logger.info(
                            "url_canonicalized_after_redirect",
                            original=self.spec.url,
                            canonical=canonical_url,
                        )
                        self.spec = self.spec.model_copy(update={"url": canonical_url})
                    monitor_config.update(detected_monitor_config)
                    if detected_monitor_config.get("challenge"):
                        with contextlib.suppress(Exception):
                            self._request_bypass_capability(
                                "generic_challenge",
                                reason="monitor_detector_challenge",
                            )
                    self.stats.recommended_monitors = list(monitors_to_try)
                    self.stats.detected_monitor_config = dict(detected_monitor_config)
                except Exception:
                    monitors_to_try = ["dom", "api_sniffer"]
                    self.stats.recommended_monitors = list(monitors_to_try)

            for fallback in ["api_sniffer", "dom"]:
                if fallback not in monitors_to_try:
                    monitors_to_try.append(fallback)
        else:
            monitors_to_try = [resolved_initial_monitor_name]
            if not self.spec.monitor and cached_strategy:
                for fallback in ["api_sniffer", "dom"]:
                    if fallback not in monitors_to_try:
                        monitors_to_try.append(fallback)

        if (
            _is_filtered_listing_url(self.spec.url)
            and self.spec.monitor in (None, "", "auto")
            and not self.spec.url_filter
        ):
            monitors_to_try = [name for name in monitors_to_try if name != "sitemap"]

        return monitors_to_try, monitor_config

    def _challenge_bypass_exhausted(self) -> bool:
        """Whether a verified anti-bot challenge exhausted every available tier.

        An empty DOM after a challenge is not evidence that a career board
        lacks links: the browser may have received another challenge page.
        Keep this strict so ordinary empty/slow sites retain their normal
        extraction outcomes.
        """
        return bool(
            self.stats.detected_monitor_config.get("challenge")
            and getattr(self.bypass_strategy, "exhausted", False)
            and int(getattr(self.bypass_strategy, "escalations_total", 0) or 0) > 0
        )

    async def _run_monitor_chain(
        self,
        monitors_to_try: list[str],
        monitor_config: dict[str, Any],
        original_http: Any,
        domain: str,
        initial_bypass: str,
    ) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        for current_monitor_name in monitors_to_try:
            attempts = self.stats.monitor_attempts
            attempts.append(current_monitor_name)
            logger.info("trying_monitor", name=current_monitor_name, url=self.spec.url)

            while True:
                try:
                    self.http = await self._apply_bypass_http(original_http)
                except Exception as exc:
                    logger.warning(
                        "bypass_apply_http_failed",
                        tier=getattr(self.bypass_strategy, "current_name", "?"),
                        error=str(exc),
                    )
                    if await self._try_escalate_bypass(exc):
                        continue
                    break

                try:
                    monitor_entry = resolve_monitor(current_monitor_name)
                except ValueError:
                    logger.error("unsupported_monitor", name=current_monitor_name)
                    break  # Try next monitor in chain

                # 2. Run monitor
                try:
                    retry_monitor_config = self._monitor_config_for_current_retry(monitor_config)
                    monitor_spec = self._runtime_monitor_spec(retry_monitor_config)
                    result = await run_monitor_attempt(
                        monitor_entry,
                        monitor_spec=monitor_spec,
                        http=self.http,
                        auth=self.auth,
                        monitor_config=retry_monitor_config,
                    )
                except Exception as exc:
                    from job_ftch.infrastructure.sources.monitors.shared import (
                        AtsRedirectException,
                        BoardGoneError,
                        BrowserChallengeError,
                    )

                    if isinstance(exc, AtsRedirectException):
                        logger.info("redirecting_to_ats", ats=exc.monitor_name, url=exc.url)
                        self.spec = self.spec.model_copy(
                            update={"url": exc.url, "monitor": exc.monitor_name}
                        )

                        # Find current index and replace all remaining monitors with just the ATS monitor
                        current_idx = monitors_to_try.index(current_monitor_name)
                        monitors_to_try[current_idx + 1 :] = [exc.monitor_name]
                        break  # Skip remaining retries and current generic monitor iteration
                    if isinstance(exc, BrowserChallengeError):
                        # A rendered challenge is affirmative evidence of an
                        # upstream protection page, not a parser failure.  A
                        # bypass tier may still recover it; otherwise stop
                        # rather than treating challenge links as candidates.
                        if await self._try_escalate_bypass(exc):
                            continue
                        self.stats.zero_reason = ZeroYieldReason.BLOCKED_NO_BYPASS_LEFT
                        return
                    if isinstance(exc, BoardGoneError):
                        logger.info("monitor_board_gone", name=current_monitor_name, url=exc.url)
                        self.stats.zero_reason = ZeroYieldReason.BOARD_GONE
                        return
                    response = (
                        getattr(exc, "response", None)
                        if isinstance(exc, httpx.HTTPStatusError)
                        else None
                    )
                    body = response.content if response is not None else None
                    if await self._try_escalate_bypass(exc, response=response, response_body=body):
                        continue
                    self.stats.zero_reason = ZeroYieldReason.BLOCKED_NO_BYPASS_LEFT
                    if _is_protected_source_error(exc):
                        # All available bypass tiers have rejected this listing.
                        # Trying the remaining generic monitors only repeats the
                        # protected request and turns a clear 403/429 outcome
                        # into a misleading timeout.
                        logger.warning(
                            "monitor_protected_terminal",
                            name=current_monitor_name,
                            url=self.spec.url,
                            error=str(exc),
                        )
                        return
                    logger.warning("monitor_run_failed", name=current_monitor_name, error=str(exc))
                    break  # Try next monitor in chain

                # 3. Apply filters/transforms EARLY to check if we found anything useful
                api_endpoint = result.metadata_updates.get("api_endpoint")
                if self._bypass_ctx and isinstance(api_endpoint, str):
                    from job_ftch.infrastructure.bypass.domain_intel import get_domain_intel

                    get_domain_intel().add_api_endpoint(domain, api_endpoint)
                result = apply_url_filter(result, self.spec.url_filter)
                result = apply_url_transform(result, self.spec.url_transform)

                if result.metadata_updates.get("confirmed_empty"):
                    # An authoritative listing explicitly says that there are
                    # no openings.  Do not turn this factual result into a
                    # generic crawl or a detail-extraction failure.
                    self.stats.zero_reason = ZeroYieldReason.CONFIRMED_EMPTY
                    return
                if result.metadata_updates.get("board_gone"):
                    self.stats.zero_reason = ZeroYieldReason.BOARD_GONE
                    return

                # 4. Check if we found anything useful. If not, and we are in auto mode, try next monitor.
                # We ignore the self-url fallback for this check.
                found_something = bool(result.payloads_by_url)
                if not found_something:
                    other_urls = {
                        u for u in result.urls if u.rstrip("/") != self.spec.url.rstrip("/")
                    }
                    found_something = bool(other_urls)

                # Browser discovery fallback is intentionally evidence-based.
                # A normal empty DOM result is common for genuinely empty boards,
                # stale URL fixtures and parser misses; it must not consume the
                # remaining source deadline by launching a browser.
                _has_url_filter = bool(
                    self.spec.url_filter or self.spec.monitor_config.get("url_filter")
                )
                _monitor_suggests_spa = bool(
                    retry_monitor_config.get("render") or self.spec.monitor_config.get("render")
                )
                if (
                    not found_something
                    and _should_escalate_empty_monitor(
                        has_url_filter=_has_url_filter,
                        monitor_suggests_spa=_monitor_suggests_spa,
                    )
                    and self._request_bypass_capability(
                        "passive_js",
                        reason=(
                            "url_filter_matched_nothing"
                            if _has_url_filter
                            else "spa_monitor_yielded_no_links"
                        ),
                    )
                ):
                    _reason = (
                        "url_filter_matched_nothing"
                        if _has_url_filter
                        else "spa_monitor_yielded_no_links"
                    )
                    logger.warning(
                        "monitor_empty_escalating",
                        reason=_reason,
                        next_tier=self.bypass_strategy.current_name,
                    )
                    continue  # retry same monitor with escalated bypass (e.g. stealth_browser)

                if not found_something and current_monitor_name != monitors_to_try[-1]:
                    self.stats.zero_reason = ZeroYieldReason.MONITOR_EMPTY
                    logger.info(
                        "monitor_yielded_no_useful_results_trying_next", name=current_monitor_name
                    )
                    break  # break inner while True, move to next monitor in outer for

                # If we got here, we either have results or we exhausted bypasses for this monitor
                # and still have no results but it's the last monitor, or we found something!

                # Phase 1 (discover): normalize the monitor result into scored,
                # deduplicated candidates with an explicit lifecycle status.
                # Monitor names describe the extraction technique, not the
                # business source. Keep metrics/dedup identity stable when a
                # declarative source omitted an explicit display name.
                from job_ftch.domain import source_spec_name

                source_name = source_spec_name(self.spec)
                candidates = self._discover_candidates(result, current_monitor_name)

                if not candidates:
                    self.stats.zero_reason = ZeroYieldReason.MONITOR_EMPTY
                    logger.info("monitor_yielded_no_valid_candidates", name=current_monitor_name)
                    break  # break inner while True, move to next monitor in outer for

                from urllib.parse import urlparse

                from job_ftch.infrastructure.bypass.humanize import get_humanizer

                get_humanizer().set_listing_url(
                    urlparse(self.spec.url).netloc.lower(), self.spec.url
                )

                # Phase 2 (enrich): emit ATS-complete candidates directly and
                # scrape URL-only candidates' detail pages (concurrently).
                scraper_chain = self._resolve_scraper_chain(current_monitor_name, monitor_config)
                # Stats are source-wide observability counters.  Success of a
                # fallback monitor must depend only on what *this* attempt
                # emitted, not on an earlier failed attempt.
                items_before = self.stats.rich_emitted + self.stats.scraped
                async for item in self._enrich_candidates(candidates, scraper_chain, source_name):
                    yield item
                attempt_items_emitted = self.stats.rich_emitted + self.stats.scraped - items_before
                if attempt_items_emitted == 0 and current_monitor_name != monitors_to_try[-1]:
                    if self.stats.zero_reason != ZeroYieldReason.BLOCKED_NO_BYPASS_LEFT:
                        self.stats.zero_reason = ZeroYieldReason.ALL_SCRAPERS_FAILED
                    logger.info(
                        "monitor_yielded_no_valid_items_after_scraping_trying_next",
                        name=current_monitor_name,
                    )
                    break

                if (
                    self.store
                    and attempt_items_emitted > 0
                    and (
                        not self.spec.bypass
                        or self.spec.bypass == "auto"
                        or not self.spec.monitor
                        or self.spec.monitor == "auto"
                    )
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

                if attempt_items_emitted > 0:
                    self.stats.final_monitor = current_monitor_name
                    if self._bypass_ctx:
                        from job_ftch.infrastructure.bypass.domain_intel import (
                            get_domain_intel,
                        )

                        intel = get_domain_intel()
                        intel.record_monitor_scraper(
                            domain,
                            current_monitor_name,
                            self.stats.successful_scraper,
                        )
                        hint = getattr(monitor_entry, "assessment_hint", None)
                        if hint is not None and hint.evidence_value == current_monitor_name:
                            intel.record_vendor(domain, current_monitor_name)
                    self.stats.zero_reason = None
                    logger.info("career_site_fetch_complete", **self.stats.to_log_dict())
                    return
                # The final monitor found listing-like candidates but could
                # not confirm a detail posting.  It is not evidence of an
                # empty career site; preserve the extraction failure instead.
                if self.stats.zero_reason != ZeroYieldReason.BLOCKED_NO_BYPASS_LEFT:
                    self.stats.zero_reason = ZeroYieldReason.ALL_SCRAPERS_FAILED
                # This is the final monitor and this attempt produced no
                # usable item.  Continuing the inner retry loop re-runs the
                # same candidates until the source deadline (especially
                # visible on broad sitemap pages) without new evidence.
                break

        if self._challenge_bypass_exhausted():
            self.stats.zero_reason = ZeroYieldReason.BLOCKED_NO_BYPASS_LEFT
        elif self.stats.zero_reason not in {
            ZeroYieldReason.ALL_SCRAPERS_FAILED,
            ZeroYieldReason.BLOCKED_NO_BYPASS_LEFT,
        }:
            self.stats.zero_reason = ZeroYieldReason.ALL_MONITORS_EXHAUSTED
        logger.info("career_site_fetch_exhausted_all_monitors", **self.stats.to_log_dict())

    async def fetch(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        self.stats = FetchStats()
        self.http = self._base_http
        self._temporary_http_clients.clear()
        self._trusted_parser_urls.clear()
        self._parser_failure_is_terminal = False
        self._detail_protection_circuit_open = False
        operation_token: Any = None
        try:
            domain, cached_strategy, original_http, initial_bypass = await self._init_strategy()
            start_operation = getattr(self.bypass_strategy, "start_operation", None)
            if callable(start_operation):
                from job_ftch.config import get_settings

                operation_token = start_operation(
                    "listing",
                    kind="listing",
                    max_browser_launches=get_settings().bypass_max_listing_browser_launches,
                )

            site_parser_emitted = False
            async for item in self._try_site_parser(original_http):
                site_parser_emitted = True
                yield item

            if site_parser_emitted or self._parser_failure_is_terminal:
                return

            await self._maybe_apply_generic_search(original_http)

            monitor_config = dict(self.spec.monitor_config)
            monitors_to_try, monitor_config = await self._resolve_monitors(
                original_http, cached_strategy, monitor_config
            )
            if monitor_config.get("board_gone"):
                self.stats.zero_reason = ZeroYieldReason.BOARD_GONE
                logger.info("career_site_board_gone_before_monitor", url=self.spec.url)
                return

            async for item in self._run_monitor_chain(
                monitors_to_try, monitor_config, original_http, domain, initial_bypass
            ):
                yield item
        finally:
            end_operation = getattr(self.bypass_strategy, "end_operation", None)
            if operation_token is not None and callable(end_operation):
                end_operation(operation_token)
            close_bypass = getattr(self.bypass_strategy, "close", None)
            if callable(close_bypass):
                close_result = close_bypass()
                if inspect.isawaitable(close_result):
                    await close_result
            await self._close_owned_http_clients()
            if self._bypass_ctx:
                try:
                    from job_ftch.infrastructure.bypass.domain_intel import get_domain_intel

                    get_domain_intel().save()
                except Exception:
                    pass

    async def _maybe_apply_generic_search(self, original_http: Any) -> None:
        """Tier-1: rewrite spec.url to a keyword-filtered search URL if possible.

        Only runs for sites with no dedicated site parser (Tier-2 parsers build
        their own search URLs) and no explicit query already in the URL. Detects
        a GET search form on the listing page and adopts it only if it verifiably
        narrows the result set. Never fails the run — on any error the original
        URL is kept.
        """
        keywords = self.spec.monitor_config.get("_search_keywords")
        if not keywords:
            return
        from job_ftch.application.registry import resolve_site_parser
        from job_ftch.infrastructure.sources.http_retry import fetch_with_retry
        from job_ftch.infrastructure.sources.site_parsers.generic_search import (
            discover_working_search_url,
        )
        from job_ftch.infrastructure.sources.site_parsers.helpers import url_has_search_query

        if resolve_site_parser(self.spec.url) is not None or url_has_search_query(self.spec.url):
            return

        try:
            client = await self._apply_bypass_http(original_http)
        except Exception:  # noqa: BLE001
            client = self.http

        async def _fetch(url: str) -> str:
            response = await fetch_with_retry(client, url, follow_redirects=True)
            response.raise_for_status()
            return str(response.text)

        try:
            search_url = await discover_working_search_url(
                _fetch, self.spec.url, keywords, log=logger
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("generic_search_apply_failed", url=self.spec.url, error=str(exc))
            return

        if search_url and search_url != self.spec.url:
            logger.info("generic_search_url_rewrite", original=self.spec.url, search_url=search_url)
            self.spec = self.spec.model_copy(update={"url": search_url})

    def _effective_limit(self) -> int | None:
        """Return the declared detail cap or MVP default."""
        if self.spec.detail_limit is not None:
            return self.spec.detail_limit
        from job_ftch.config import get_settings

        settings = get_settings()
        if settings.career_site_default_detail_limit is not None:
            return settings.career_site_default_detail_limit
        if settings.career_site_window_max_details:
            return settings.career_site_window_max_details
        return 100  # Conservative hard default for MVP

    async def _iter_scraped_detail_items(
        self,
        urls: list[str],
        scraper_chain: list[str],
        source_name: str,
    ) -> AsyncIterator[RawItem]:
        from job_ftch.config import get_settings

        limit = self._effective_limit()
        if limit is not None and len(urls) > limit:
            self.stats.truncated = True
        selected_urls = urls if limit is None else urls[:limit]
        if not selected_urls:
            return

        settings = get_settings()
        concurrency = min(
            settings.career_site_detail_concurrency,
            settings.career_site_global_detail_concurrency,
            len(selected_urls),
        )
        if concurrency <= 1:
            for url in selected_urls:
                try:
                    item = await self._scrape_detail_url_to_raw_item(
                        url, scraper_chain, source_name
                    )
                except Exception as exc:
                    logger.warning("detail_scrape_failed", url=url, error=str(exc))
                    continue
                if item is not None:
                    self.stats.scraped += 1
                    yield item
            return

        _SENTINEL = object()
        result_queue: asyncio.Queue[RawItem | object] = asyncio.Queue(maxsize=concurrency * 2)
        url_queue: asyncio.Queue[str] = asyncio.Queue()
        for url in selected_urls:
            url_queue.put_nowait(url)

        async def _worker() -> None:
            while True:
                try:
                    next_url = url_queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    item = await self._scrape_detail_url_to_raw_item(
                        next_url,
                        scraper_chain,
                        source_name,
                    )
                except Exception as exc:
                    logger.warning("detail_scrape_failed", url=next_url, error=str(exc))
                    continue
                if item is not None:
                    await result_queue.put(item)

        async def _run_workers() -> None:
            async with asyncio.TaskGroup() as tg:
                for _ in range(concurrency):
                    tg.create_task(_worker())
            await result_queue.put(_SENTINEL)

        producer = asyncio.create_task(_run_workers())
        try:
            while True:
                queued_item = await result_queue.get()
                if queued_item is _SENTINEL:
                    break
                self.stats.scraped += 1
                yield cast("RawItem", queued_item)
        finally:
            if not producer.done():
                producer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await producer

    async def _filter_unprocessed_urls(
        self,
        urls: set[str],
        *,
        source_name: str,
    ) -> set[str]:
        """Keep every discovered URL until its current content is available.

        A processed locator can point to an updated posting.  Suppressing it
        before detail fetch would make changed content invisible, so exact
        content suppression is deliberately deferred to the pipeline.
        """
        del source_name
        return set(urls)

    def _discover_candidates(
        self,
        result: Any,
        monitor_name: str,
    ) -> list[DiscoveredCandidate]:
        """Phase 1: turn a raw MonitorResult into scored lifecycle candidates.

        Rich (ATS) payloads carry full fields already; URL-only entries are
        listing links that still need a detail-page fetch in phase 2. The
        ``monitor_type`` tag is threaded into payload metadata so the
        downstream ``CompletenessGateNode`` can trust structured ATS output.
        """
        candidates: list[DiscoveredCandidate] = []

        rich_urls: set[str] = set()
        for url, payload in (result.payloads_by_url or {}).items():
            if url not in result.urls:
                continue
            if not self._passes_freshness_cutoff(payload.date_posted):
                continue
            if payload.metadata is None:
                payload.metadata = {}
            completeness = score_completeness(payload)
            payload.metadata["monitor_type"] = monitor_name
            payload.metadata["heuristic_completeness"] = completeness
            candidates.append(
                DiscoveredCandidate(
                    url=url,
                    rich_payload=payload,
                    completeness=completeness,
                )
            )
            rich_urls.add(url)

        urls_to_scrape = result.urls - rich_urls
        if not urls_to_scrape and self._should_scrape_source_url():
            urls_to_scrape = {self.spec.url}
        if monitor_name == "sitemap":
            # A corporate sitemap is a broad site inventory, not a vacancy
            # listing.  Its frontier must contain only technically plausible
            # detail locators; ATS parsers and DOM monitors retain their own
            # richer discovery semantics.
            urls_to_scrape = {
                url
                for url in urls_to_scrape
                if score_job_url(url, board_url=self.spec.url) >= _SITEMAP_DETAIL_SCORE_MINIMUM
            }
        effective_limit = self._effective_limit()
        candidate_cap = (
            len(urls_to_scrape)
            if effective_limit is None
            else max(
                effective_limit * _GENERIC_CANDIDATE_MULTIPLIER,
                _GENERIC_CANDIDATE_FLOOR,
            )
        )
        if len(urls_to_scrape) > candidate_cap:
            # This is technical URL triage, not vacancy relevance filtering:
            # retain the strongest detail-page locators before applying the
            # bounded frontier.  Alphabetical truncation selected product and
            # blog paths from large corporate sitemaps.
            urls_to_scrape = set(_rank_detail_urls(urls_to_scrape, self.spec.url)[:candidate_cap])
            self.stats.truncated = True
            self.stats.monitor_truncated = 1
        candidates.extend(DiscoveredCandidate(url=url, completeness=0.1) for url in urls_to_scrape)

        self.stats.monitored = len(result.urls)
        # A bounded monitor inventory is not a failed/interrupted stream.
        self.stats.truncated = self.stats.truncated or result.truncated
        if result.truncated:
            self.stats.monitor_truncated = 1

        return candidates

    async def _enrich_candidates(
        self,
        candidates: list[DiscoveredCandidate],
        scraper_chain: list[str],
        source_name: str,
    ) -> AsyncIterator[RawItem]:
        """Phase 2: emit ATS-complete candidates directly; scrape the rest.

        Rich candidates already carry all fields, so they are emitted without a
        detail-page round trip. URL-only candidates are pre-deduped, ranked, and
        scraped through the shared concurrent detail-page worker pool.
        """
        rich = [c for c in candidates if c.rich_payload is not None]
        if self.spec.limit is not None and len(rich) > self.spec.limit:
            rich = rich[: self.spec.limit]
            self.stats.truncated = True
        for candidate in rich:
            candidate.status = IngestItemStatus.NEW
            self.stats.rich_emitted += 1
            payload = candidate.rich_payload
            if payload is None:
                continue
            if self._bypass_ctx:
                self._bypass_ctx.record_success(candidate.url)
            yield payload_to_raw_item(payload, self.spec, source_name)

        urls_to_scrape = {c.url for c in candidates if c.rich_payload is None}
        if not urls_to_scrape:
            return

        # Pre-dedup: drop URLs already persisted as processed before committing
        # to detail-page scrape requests. Fail-open so a store outage never
        # silently empties the run.
        if self.store is not None and hasattr(self.store, "has_processed"):
            urls_to_scrape = await self._filter_unprocessed_urls(
                urls_to_scrape, source_name=source_name
            )

        ranked = _rank_detail_urls(urls_to_scrape, self.spec.url)
        async for item in self._iter_scraped_detail_items(ranked, scraper_chain, source_name):
            yield item

    async def _scrape_detail_url_to_raw_item(
        self,
        url: str,
        scraper_chain: list[str],
        source_name: str,
    ) -> RawItem | None:
        """Run one detail operation with an independent task-local budget."""
        start_operation = getattr(self.bypass_strategy, "start_operation", None)
        end_operation = getattr(self.bypass_strategy, "end_operation", None)
        token: Any = None
        if callable(start_operation):
            from job_ftch.config import get_settings

            token = start_operation(
                f"detail:{hashlib.sha256(url.encode()).hexdigest()[:12]}",
                kind="detail",
                max_browser_launches=get_settings().bypass_max_detail_browser_launches,
            )
        try:
            return await self._scrape_detail_url_to_raw_item_inner(
                url,
                scraper_chain,
                source_name,
            )
        finally:
            if token is not None and callable(end_operation):
                end_operation(token)

    async def _scrape_detail_url_to_raw_item_inner(
        self,
        url: str,
        scraper_chain: list[str],
        source_name: str,
    ) -> RawItem | None:
        if self._detail_protection_circuit_open:
            return None
        explicitly_included_self = bool(self.spec.monitor_config.get("include_self_url")) and (
            url.rstrip("/").casefold() == self.spec.url.rstrip("/").casefold()
        )
        if (
            url not in self._trusted_parser_urls
            and not explicitly_included_self
            and not _is_valid_detail_candidate(url, self.spec.url)
        ):
            logger.debug("detail_candidate_rejected", url=url, board_url=self.spec.url)
            return None
        self.stats.detail_attempted += 1
        from job_ftch.config import get_settings

        async with detail_slot(get_settings().career_site_global_detail_concurrency):
            scrape_result = await self._scrape_with_fallback(url, scraper_chain)
        if not scrape_result:
            return None
        if not (scrape_result.title or scrape_result.description):
            return None
        if not scrape_result.description and url not in self._trusted_parser_urls:
            # A generic scraper can recover a page title from arbitrary
            # editorial, marketing, or tag pages.  Without a description it
            # carries no evidence that the page is a vacancy, so do not emit a
            # false positive.  Parser-discovered URLs remain exempt because a
            # site-specific parser already established their posting identity.
            logger.debug("detail_candidate_rejected_title_only", url=url)
            return None
        if not self._passes_freshness_cutoff(scrape_result.date_posted):
            return None
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
            metadata={**(scrape_result.metadata or {}), "detail_vacancy_confirmed": True},
        )
        return payload_to_raw_item(full_payload, self.spec, source_name)

    def _passes_freshness_cutoff(self, date_posted: str | None) -> bool:
        from job_ftch.config import get_settings

        cutoff = self.spec.freshness_cutoff_utc
        if cutoff is None:
            return True
        parsed = _parse_source_datetime(date_posted)
        if parsed is None:
            if get_settings().freshness_require_date:
                self.stats.freshness_filtered += 1
                return False
            self.stats.freshness_undated_passed += 1
            logger.debug(
                "freshness_cutoff_undated_pass", source=self.spec.source_name or self.spec.url
            )
            return True
        normalized_cutoff = cutoff if cutoff.tzinfo is not None else cutoff.replace(tzinfo=UTC)
        if parsed < normalized_cutoff.astimezone(UTC):
            self.stats.freshness_filtered += 1
            return False
        return True

    def _resolve_scraper_chain(
        self, monitor_name: str, monitor_config: dict[str, Any]
    ) -> list[str]:
        """Determine primary and fallback scrapers based on monitor type."""
        if self.spec.scraper:
            chain = [self.spec.scraper]
            chain.extend(self.spec.scraper_fallback)
            return chain

        try:
            declared = list(resolve_monitor(monitor_name).scraper_chain)
        except ValueError:
            declared = []
        chain = declared or ["json-ld", "embedded", "nextdata", "dom", "maintext"]
        if ("xpath" in self.spec.scraper_config or "xpath_rules" in self.spec.scraper_config) and (
            "xpath" not in chain
        ):
            chain.insert(0, "xpath")
        return chain

    def _should_scrape_source_url(self) -> bool:
        include_self = self.spec.monitor_config.get("include_self_url", False)
        if include_self:
            return True

        # Only self-scrape when the configured URL is a SPECIFIC posting (a job
        # marker followed by an id-bearing segment, e.g. /jobs/84179-foo or
        # /vacancy/12345). A listing/search entry point (/search/vacancy,
        # /vacancies, /jobs/, /job/openings) must NOT be scraped as a single job:
        # that yields page chrome ("954 329 вакансий", "132 вакансії") instead of
        # a vacancy. Detail URLs found via discovery are scraped separately.
        return bool(_DETAIL_URL_RE.search(self.spec.url))

    def _should_render_detail(self) -> bool:
        return bool(
            self.spec.scraper_config.get("render") or self.spec.monitor_config.get("render")
        )

    async def _fetch_detail_html_with_browser(self, url: str) -> str | None:
        try:
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
        browser_config["_bypass_strategy"] = self.bypass_strategy
        browser_config["_pipeline_stats"] = self.stats

        try:
            async with open_page(browser_config, bypass_strategy=self.bypass_strategy) as page:
                await await_with_source_deadline(navigate(page, url, browser_config))
                settle_seconds = float(
                    self.spec.scraper_config.get(
                        "settle_seconds",
                        self.spec.monitor_config.get("settle_seconds", 0),
                    )
                )
                if settle_seconds > 0:
                    await sleep_with_source_deadline(settle_seconds)
                return str(await await_with_source_deadline(page.content()))
        except Exception as exc:
            logger.debug("detail_browser_prefetch_failed", url=url, error=str(exc))
            return None

    async def _run_scraper_chain(
        self,
        scraper_chain: list[str],
        url: str,
        prefetched_html: str | None,
        scrape_http: Any,
        *,
        count_first_as_fallback: bool = False,
        log_event: str = "scraper_failed",
    ) -> ScrapedPostingPayload | None:
        """Try each scraper in *scraper_chain* with *prefetched_html* until one succeeds."""
        title_only_fallback: ScrapedPostingPayload | None = None
        for i, scraper_name in enumerate(scraper_chain):
            try:
                typed_payload, prefetched_html = await run_scraper_attempt(
                    scraper_name,
                    url=url,
                    base_config=dict(self.spec.scraper_config or {}),
                    prefetched_html=prefetched_html,
                    http=scrape_http,
                    fetch_browser_html=lambda: self._fetch_detail_html_with_browser(url),
                    resolver=resolve_scraper,
                )
                if typed_payload and (typed_payload.description or "").strip():
                    self.stats.successful_scraper = scraper_name
                    if i > 0 or count_first_as_fallback:
                        self.stats.scrape_fallback_used += 1
                    return typed_payload
                if typed_payload and typed_payload.title and title_only_fallback is None:
                    title_only_fallback = typed_payload
            except Exception as exc:
                logger.debug(log_event, name=scraper_name, url=url, error=str(exc))
                continue
        return title_only_fallback

    async def _scrape_with_fallback(
        self, url: str, scraper_chain: list[str]
    ) -> ScrapedPostingPayload | None:
        """Try scrapers in order until one returns a result with content."""
        prefetched_html: str | None = None
        response = None
        rendered_with_browser = False
        protected_failure = False
        async with client_for_config(self.http, self.spec.scraper_config) as scrape_http:
            try:
                response = await scrape_http.get(url, follow_redirects=True)
                prefetched_html = getattr(response, "text", None)
            except Exception as exc:
                protected_failure = _is_protected_source_error(exc)
                prefetched_html = None

            if prefetched_html and response is not None:
                from job_ftch.infrastructure.bypass.failure_signal import (
                    FailureKind,
                    HeuristicFailureSignal,
                )

                _body = prefetched_html.encode("utf-8", errors="ignore")
                retry_after = _parse_retry_after(
                    getattr(response, "headers", {}).get("Retry-After")
                )
                _kind = HeuristicFailureSignal().classify(
                    status_code=getattr(response, "status_code", None),
                    body=_body,
                    error=None,
                )
                if _kind in (
                    FailureKind.CAPTCHA,
                    FailureKind.CHALLENGE,
                    FailureKind.BLOCKED,
                    FailureKind.BLOCKED_IP,
                    FailureKind.BLOCKED_FINGERPRINT,
                ):
                    logger.warning(
                        "access_denied_on_detail_page",
                        kind=_kind,
                        url=url,
                        status_code=getattr(response, "status_code", None),
                    )
                    # Register with bypass manager → immediate escalation
                    if hasattr(self.bypass_strategy, "handle_failure"):
                        try:
                            failure_result = self.bypass_strategy.handle_failure(
                                url,
                                status_code=getattr(response, "status_code", None),
                                body=_body,
                                error=None,
                                retry_after=retry_after,
                            )
                        except TypeError:
                            failure_result = self.bypass_strategy.handle_failure(
                                url,
                                status_code=getattr(response, "status_code", None),
                                body=_body,
                                error=None,
                            )
                        if inspect.isawaitable(failure_result):
                            await failure_result
                    # Retry with browser (escalated tier is now cloak/stealth_browser)
                    browser_html = await self._fetch_detail_html_with_browser(url)
                    if browser_html:
                        # A browser can successfully navigate to the CDN page
                        # while still being stopped by its CAPTCHA/challenge.
                        # Treat that response as protection, not as a failed
                        # vacancy parser attempt.  Otherwise every protected
                        # detail URL is needlessly fed through the scraper
                        # chain and the source is incorrectly reported as a
                        # generic detail-extraction failure.
                        browser_kind = HeuristicFailureSignal().classify(
                            status_code=200,
                            body=browser_html.encode("utf-8", errors="ignore"),
                            error=None,
                        )
                        if browser_kind in (
                            FailureKind.CAPTCHA,
                            FailureKind.CHALLENGE,
                            FailureKind.BLOCKED,
                            FailureKind.BLOCKED_IP,
                            FailureKind.BLOCKED_FINGERPRINT,
                        ):
                            logger.warning(
                                "access_denied_in_browser_detail_page",
                                kind=browser_kind,
                                url=url,
                            )
                            self._record_detail_protection_failure()
                            return None
                        prefetched_html = browser_html
                        rendered_with_browser = True
                    else:
                        logger.warning("access_denied_no_browser_fallback", kind=_kind, url=url)
                        self._record_detail_protection_failure()
                        return None

            if self._should_render_detail():
                browser_html = await self._fetch_detail_html_with_browser(url)
                if browser_html:
                    prefetched_html = browser_html
                    rendered_with_browser = True

            result = await self._run_scraper_chain(scraper_chain, url, prefetched_html, scrape_http)
            if result is not None and not (result.description or "").strip():
                visible_description = _visible_description_fallback(prefetched_html)
                if visible_description and not _declares_editorial_article(prefetched_html):
                    result = replace(result, description=visible_description)
            # An embedded hydration blob often contains a title but omits the
            # vacancy body.  Do not treat that as a complete detail page: the
            # rendered page can still supply the description (notably on SPA
            # career boards).  Keep the title-only result as a safe fallback
            # if rendering is unavailable or yields nothing better.
            has_description = bool(result and (result.description or "").strip())
            should_retry_in_browser = not rendered_with_browser and (
                not has_description or (prefetched_html and _looks_like_spa_shell(prefetched_html))
            )

            if should_retry_in_browser:
                browser_html = await self._fetch_detail_html_with_browser(url)
                if browser_html and browser_html != prefetched_html:
                    browser_result = await self._run_scraper_chain(
                        scraper_chain,
                        url,
                        browser_html,
                        scrape_http,
                        count_first_as_fallback=True,
                        log_event="scraper_failed_after_browser_retry",
                    )
                    if browser_result is not None:
                        return browser_result

            if result is not None:
                return result

        if protected_failure:
            self._record_detail_protection_failure()
        logger.warning("all_scrapers_failed", url=url, chain=scraper_chain)
        return None

    def _record_detail_protection_failure(self) -> None:
        from job_ftch.config import get_settings

        self.stats.detail_protection_failures += 1
        # The source-level outcome is already known after a confirmed
        # access-control response.  The configurable threshold below controls
        # request suppression only; it must not change a protected source into
        # ``all_scrapers_failed`` merely because it exposed fewer detail URLs
        # than the circuit-breaker threshold.
        self.stats.zero_reason = ZeroYieldReason.BLOCKED_NO_BYPASS_LEFT
        if (
            self.stats.detail_protection_failures
            < get_settings().career_site_protection_failure_limit
        ):
            return
        self._detail_protection_circuit_open = True
        self.stats.protection_circuit_open = True
        self.stats.zero_reason = ZeroYieldReason.BLOCKED_NO_BYPASS_LEFT
        logger.warning(
            "detail_protection_circuit_open",
            url=self.spec.url,
            failures=self.stats.detail_protection_failures,
        )


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
        own_http_client=True,
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
            limit=settings.career_site_default_limit,
        )
    )
