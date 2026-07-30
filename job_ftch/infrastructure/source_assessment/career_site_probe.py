"""Reusable bounded assessment probes for career-site freshness signals."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from job_ftch.application.registry import (
    SourceRegistryAssessmentHint,
    get_all_monitor_entries,
    get_all_scraper_entries,
)
from job_ftch.domain.source_assessment import (
    AssessmentConfidence,
    FreshnessAssessment,
    SourceCapabilities,
    SourceEvidence,
)
from job_ftch.infrastructure.sources.monitors.dom import extract_static_job_links
from job_ftch.infrastructure.sources.site_defaults import apply_runtime_defaults
from job_ftch.infrastructure.sources.site_fingerprinter import SiteClass, fingerprint
from job_ftch.infrastructure.sources.source_policy import resolve_source_policy

if TYPE_CHECKING:
    from collections.abc import Iterable

    from job_ftch.domain.source_spec import CareerSiteSpec


_PROBE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
)
_PROBE_TIMEOUT = httpx.Timeout(8.0, connect=4.0)
_MAX_HTML_CHARS = 300_000
_DETAIL_SAMPLE_SIZE = 3
_STATIC_SCRAPER_PROBES = frozenset({"json-ld", "embedded", "nextdata", "dom"})

_DATE_KEY_RE = re.compile(
    r"(dateposted|datemodified|date_posted|published(?:at|_at)?|posted(?:at|_at)?|"
    r"created(?:at|_at)?|releaseddate|first_published)",
    re.IGNORECASE,
)
_DATE_TEXT_RE = re.compile(
    r"(\bdateposted\b|\bdatemodified\b|<time\b[^>]*\bdatetime=|\bdatetime=|"
    r'"(?:published|published_at|publishedAt|posted|posted_at|createdAt|created_at)"\s*:|'
    r"\bопублик)",
    re.IGNORECASE,
)
_ORDERED_RE = re.compile(
    r"([?&](?:sort|order|order_by)=|[\"'](?:sort|order|orderBy|order_by)[\"']\s*:|"
    r"\bsort by\b|\bsorting\b|order_by=publication_time|\bnewest\b|\blatest\b|"
    r"сначала новые|published within last)",
    re.IGNORECASE,
)
_RSS_OR_SITEMAP_RE = re.compile(
    r"(application/(?:rss|atom)\+xml|/rss\b|/feed\b|/sitemap)", re.IGNORECASE
)


@dataclass(frozen=True)
class CareerSiteProbeResult:
    capabilities: SourceCapabilities
    evidence: tuple[SourceEvidence, ...]
    freshness: FreshnessAssessment


def _confidence_rank(value: AssessmentConfidence) -> int:
    return {
        AssessmentConfidence.LOW: 0,
        AssessmentConfidence.MEDIUM: 1,
        AssessmentConfidence.HIGH: 2,
    }[value]


def _max_confidence(
    left: AssessmentConfidence, right: AssessmentConfidence
) -> AssessmentConfidence:
    return left if _confidence_rank(left) >= _confidence_rank(right) else right


def _merge_evidence(items: Iterable[SourceEvidence]) -> tuple[SourceEvidence, ...]:
    seen: set[tuple[str, str]] = set()
    merged: list[SourceEvidence] = []
    for item in items:
        key = (item.kind, item.value)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return tuple(merged)


def _has_date_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if _DATE_KEY_RE.search(str(key)):
                return True
            if _has_date_key(nested):
                return True
    elif isinstance(value, list):
        return any(_has_date_key(item) for item in value)
    elif isinstance(value, str):
        return bool(_DATE_TEXT_RE.search(value))
    return False


def _has_order_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if _ORDERED_RE.search(str(key)):
                return True
            if _has_order_key(nested):
                return True
    elif isinstance(value, list):
        return any(_has_order_key(item) for item in value)
    elif isinstance(value, str):
        return bool(_ORDERED_RE.search(value))
    return False


def _hint_evidence(hint: SourceRegistryAssessmentHint, *, component: str) -> SourceEvidence:
    return SourceEvidence(
        kind=f"{component}_hint",
        value=hint.evidence_value,
        confidence=hint.confidence,
    )


def _hint_capabilities(hint: SourceRegistryAssessmentHint) -> SourceCapabilities:
    return SourceCapabilities(
        source_family=hint.source_family,
        has_publication_time=hint.has_publication_time,
        has_update_time=hint.has_update_time,
        has_stable_id=hint.has_stable_id,
        has_stable_url=hint.has_stable_url,
        supports_ordered_head=hint.supports_ordered_head,
        has_cursor_or_since_filter=hint.has_cursor_or_since_filter,
        has_change_validators=hint.has_change_validators,
        has_page_change_signal=hint.has_page_change_signal,
        has_rss_or_sitemap_dates=hint.has_rss_or_sitemap_dates,
        has_embedded_state=hint.has_embedded_state,
        known_integration=hint.known_integration,
    )


def _freshness_from_capabilities(
    capabilities: SourceCapabilities,
    *,
    confidence: AssessmentConfidence,
    probe_failed: bool = False,
    probe_blocked: bool = False,
    dates_require_detail_scrape: bool = False,
) -> FreshnessAssessment:
    if capabilities.has_publication_time or capabilities.has_update_time:
        return FreshnessAssessment(
            confidence=confidence,
            can_detect_freshness_without_snapshot=True,
            can_filter_since_yesterday=True,
            item_level_dates=True,
            ordered_by_newest=capabilities.supports_ordered_head,
            requires_full_snapshot=False,
            dates_require_detail_scrape=dates_require_detail_scrape,
            probe_failed=probe_failed,
            probe_blocked=probe_blocked,
            rationale="Source exposes item-level publication or update timestamps.",
        )
    if capabilities.supports_ordered_head or capabilities.has_cursor_or_since_filter:
        return FreshnessAssessment(
            confidence=confidence,
            can_detect_freshness_without_snapshot=True,
            can_filter_since_yesterday=capabilities.has_cursor_or_since_filter,
            ordered_by_newest=capabilities.supports_ordered_head,
            requires_full_snapshot=False,
            dates_require_detail_scrape=dates_require_detail_scrape,
            probe_failed=probe_failed,
            probe_blocked=probe_blocked,
            rationale="Source exposes a bounded freshness signal through ordered listing head or cursor filters.",
        )
    if capabilities.has_page_change_signal or capabilities.has_rss_or_sitemap_dates:
        return FreshnessAssessment(
            confidence=confidence,
            can_detect_freshness_without_snapshot=True,
            page_level_change_only=True,
            requires_full_snapshot=False,
            dates_require_detail_scrape=dates_require_detail_scrape,
            probe_failed=probe_failed,
            probe_blocked=probe_blocked,
            rationale="Source exposes page-level change metadata, but not per-item dates.",
        )
    return FreshnessAssessment(
        confidence=confidence,
        can_detect_freshness_without_snapshot=False,
        can_filter_since_yesterday=False,
        requires_full_snapshot=True,
        dates_require_detail_scrape=dates_require_detail_scrape,
        probe_failed=probe_failed,
        probe_blocked=probe_blocked,
        rationale="No reliable freshness signal is visible from bounded source assessment probes.",
    )


def _merge_capabilities(left: SourceCapabilities, right: SourceCapabilities) -> SourceCapabilities:
    return SourceCapabilities(
        source_family=right.source_family
        if right.source_family != "unknown"
        else left.source_family,
        has_publication_time=left.has_publication_time or right.has_publication_time,
        has_update_time=left.has_update_time or right.has_update_time,
        has_stable_id=left.has_stable_id or right.has_stable_id,
        has_stable_url=left.has_stable_url or right.has_stable_url,
        supports_ordered_head=left.supports_ordered_head or right.supports_ordered_head,
        has_cursor_or_since_filter=(
            left.has_cursor_or_since_filter or right.has_cursor_or_since_filter
        ),
        has_change_validators=left.has_change_validators or right.has_change_validators,
        has_page_change_signal=left.has_page_change_signal or right.has_page_change_signal,
        has_rss_or_sitemap_dates=(left.has_rss_or_sitemap_dates or right.has_rss_or_sitemap_dates),
        has_embedded_state=left.has_embedded_state or right.has_embedded_state,
        known_integration=left.known_integration or right.known_integration,
    )


def _evidence_from_probe_output(
    output: Any,
    *,
    component: str,
    confidence: AssessmentConfidence = AssessmentConfidence.MEDIUM,
) -> tuple[SourceEvidence, ...]:
    if output is None:
        return ()
    if isinstance(output, SourceEvidence):
        return (output,)
    if isinstance(output, (list, tuple)):
        return tuple(item for item in output if isinstance(item, SourceEvidence))
    if isinstance(output, dict):
        return (
            SourceEvidence(
                kind=f"{component}_probe",
                value="matched",
                confidence=confidence,
                details={str(key): value for key, value in output.items()},
            ),
        )
    return (
        SourceEvidence(
            kind=f"{component}_probe",
            value=str(output)[:120],
            confidence=confidence,
        ),
    )


async def _call_assessment_probe(callback: Any, **kwargs: Any) -> Any:
    try:
        sig = inspect.signature(callback)
        supported = {
            key: value
            for key, value in kwargs.items()
            if key in sig.parameters
            or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        }
    except (TypeError, ValueError):
        supported = kwargs
    result = callback(**supported)
    if inspect.isawaitable(result):
        return await result
    return result


class CareerSiteAssessmentEngine:
    """Collect bounded freshness evidence from existing career-site infrastructure."""

    async def assess(self, spec: CareerSiteSpec) -> CareerSiteProbeResult:
        normalized_spec = apply_runtime_defaults(spec)
        source_policy = resolve_source_policy(normalized_spec.url)
        evidence: list[SourceEvidence] = []
        capabilities = SourceCapabilities(
            source_family=source_policy.family,
            has_stable_url=True,
        )
        confidence = AssessmentConfidence.MEDIUM
        probe_failed = False
        probe_blocked = False

        headers = {
            "User-Agent": _PROBE_USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
        }
        try:
            from job_ftch.infrastructure.sources.ssrf_guard import SSRFGuardedTransport

            async with httpx.AsyncClient(
                headers=headers,
                timeout=_PROBE_TIMEOUT,
                follow_redirects=True,
                transport=SSRFGuardedTransport(httpx.AsyncHTTPTransport()),
            ) as client:
                try:
                    profile = await fingerprint(normalized_spec.url, client)
                    evidence.append(
                        SourceEvidence(
                            kind="site_fingerprint",
                            value=profile.site_class.value,
                            confidence=AssessmentConfidence.MEDIUM,
                            details={
                                "recommended_monitors": list(profile.recommended_monitors),
                                "detected_config": dict(profile.detected_config),
                            },
                        )
                    )
                    if profile.site_class == SiteClass.RSS:
                        capabilities = _merge_capabilities(
                            capabilities,
                            SourceCapabilities(
                                source_family="rss_like_site",
                                has_rss_or_sitemap_dates=True,
                                has_stable_url=True,
                            ),
                        )
                    elif profile.site_class == SiteClass.SPA:
                        capabilities = _merge_capabilities(
                            capabilities,
                            SourceCapabilities(has_embedded_state=True, has_stable_url=True),
                        )
                    elif profile.site_class == SiteClass.BLOCKED:
                        probe_blocked = True
                except Exception as exc:
                    probe_failed = True
                    evidence.append(
                        SourceEvidence(
                            kind="probe_status",
                            value="fingerprint_failed",
                            confidence=AssessmentConfidence.LOW,
                            details={"error": type(exc).__name__},
                        )
                    )
                    profile = None

                response = await client.get(normalized_spec.url, follow_redirects=True)
                html = response.text[:_MAX_HTML_CHARS]
                final_url = str(response.url)
                combined = f"{final_url}\n{html}"
                evidence.append(
                    SourceEvidence(
                        kind="source_policy",
                        value=source_policy.policy_name,
                        confidence=AssessmentConfidence.MEDIUM,
                        details={
                            "family": source_policy.family,
                            "allows_generic_job_pipeline": source_policy.allows_generic_job_pipeline,
                        },
                    )
                )

                if response.status_code in (401, 403, 429, 503):
                    probe_blocked = True
                    evidence.append(
                        SourceEvidence(
                            kind="probe_status",
                            value="blocked_status",
                            confidence=AssessmentConfidence.MEDIUM,
                            details={"status_code": response.status_code},
                        )
                    )

                surface_capabilities = SourceCapabilities(
                    source_family="generic_site",
                    has_stable_url=True,
                    has_publication_time=bool(_DATE_TEXT_RE.search(combined)),
                    has_update_time=bool(_DATE_TEXT_RE.search(combined)),
                    supports_ordered_head=bool(_ORDERED_RE.search(combined)),
                    has_change_validators=bool(
                        response.headers.get("last-modified") or response.headers.get("etag")
                    ),
                    has_page_change_signal=bool(response.headers.get("last-modified")),
                    has_rss_or_sitemap_dates=bool(_RSS_OR_SITEMAP_RE.search(combined)),
                    has_embedded_state=any(
                        token in html for token in ("__NEXT_DATA__", "__NUXT__", "apollo-state")
                    ),
                )
                capabilities = _merge_capabilities(capabilities, surface_capabilities)
                evidence.extend(self._surface_evidence(surface_capabilities, response))

                recommended = set(profile.recommended_monitors if profile else ())
                explicit_monitor = normalized_spec.monitor
                if explicit_monitor and explicit_monitor != "auto":
                    recommended.add(explicit_monitor)
                await self._collect_monitor_evidence(
                    normalized_spec,
                    client,
                    html,
                    final_url,
                    recommended,
                    evidence,
                    lambda item: self._merge_into(capabilities, item),
                )
                capabilities = self._capabilities_from_evidence(capabilities, evidence)

                has_strong_signal_before_scraper = (
                    capabilities.has_publication_time
                    or capabilities.has_update_time
                    or capabilities.supports_ordered_head
                    or capabilities.has_cursor_or_since_filter
                )
                if not has_strong_signal_before_scraper:
                    detail_urls = extract_static_job_links(
                        html,
                        final_url,
                        url_filter=normalized_spec.url_filter
                        or normalized_spec.monitor_config.get("url_filter"),
                        limit=_DETAIL_SAMPLE_SIZE,
                    )
                    await self._collect_scraper_evidence(
                        client,
                        final_url,
                        html,
                        detail_urls,
                        evidence,
                    )
                    capabilities = self._capabilities_from_evidence(capabilities, evidence)

                has_strong_signal_after_scraper = (
                    capabilities.has_publication_time
                    or capabilities.has_update_time
                    or capabilities.supports_ordered_head
                    or capabilities.has_cursor_or_since_filter
                )
                dates_require_detail_scrape = (
                    not has_strong_signal_before_scraper
                ) and has_strong_signal_after_scraper

        except Exception as exc:
            probe_failed = True
            evidence.append(
                SourceEvidence(
                    kind="probe_status",
                    value="surface_probe_failed",
                    confidence=AssessmentConfidence.LOW,
                    details={"error": type(exc).__name__},
                )
            )
            confidence = AssessmentConfidence.LOW

        freshness = _freshness_from_capabilities(
            capabilities,
            confidence=confidence,
            probe_failed=probe_failed,
            probe_blocked=probe_blocked,
            dates_require_detail_scrape=locals().get("dates_require_detail_scrape", False),
        )
        return CareerSiteProbeResult(
            capabilities=capabilities,
            evidence=_merge_evidence(evidence),
            freshness=freshness,
        )

    def _surface_evidence(
        self, capabilities: SourceCapabilities, response: httpx.Response
    ) -> list[SourceEvidence]:
        evidence: list[SourceEvidence] = []
        if capabilities.has_publication_time:
            evidence.append(
                SourceEvidence(
                    kind="surface_signal",
                    value="item_datetime",
                    confidence=AssessmentConfidence.MEDIUM,
                )
            )
        if capabilities.supports_ordered_head:
            evidence.append(
                SourceEvidence(
                    kind="surface_signal",
                    value="ordered_listing",
                    confidence=AssessmentConfidence.MEDIUM,
                )
            )
        if capabilities.has_embedded_state:
            evidence.append(
                SourceEvidence(
                    kind="surface_signal",
                    value="embedded_state",
                    confidence=AssessmentConfidence.MEDIUM,
                )
            )
        if capabilities.has_page_change_signal:
            evidence.append(
                SourceEvidence(
                    kind="surface_signal",
                    value="last_modified_header",
                    confidence=AssessmentConfidence.MEDIUM,
                )
            )
        if response.headers.get("etag"):
            evidence.append(
                SourceEvidence(
                    kind="surface_signal",
                    value="etag_header",
                    confidence=AssessmentConfidence.MEDIUM,
                )
            )
        if capabilities.has_rss_or_sitemap_dates:
            evidence.append(
                SourceEvidence(
                    kind="surface_signal",
                    value="rss_or_sitemap",
                    confidence=AssessmentConfidence.MEDIUM,
                )
            )
        return evidence

    async def _collect_monitor_evidence(
        self,
        spec: CareerSiteSpec,
        client: httpx.AsyncClient,
        html: str,
        final_url: str,
        recommended: set[str],
        evidence: list[SourceEvidence],
        merge_capabilities: Any,
    ) -> None:
        del merge_capabilities
        for entry in get_all_monitor_entries():
            hint_matches_url = bool(
                entry.assessment_hint
                and any(
                    re.search(pattern, spec.url, re.IGNORECASE)
                    or re.search(pattern, final_url, re.IGNORECASE)
                    or re.search(pattern, html, re.IGNORECASE)
                    for pattern in entry.assessment_hint.url_patterns
                )
            )
            should_probe = entry.name in recommended or hint_matches_url
            if not should_probe:
                continue
            try:
                if entry.assessment_probe is not None and should_probe:
                    output = await _call_assessment_probe(
                        entry.assessment_probe,
                        url=spec.url,
                        spec=spec,
                        client=client,
                        html=html,
                        final_url=final_url,
                    )
                elif entry.can_handle is not None:
                    output = await entry.can_handle(spec.url, client)
                else:
                    output = None
            except Exception:
                continue

            if output is None:
                continue

            evidence.extend(
                _evidence_from_probe_output(
                    output,
                    component=f"monitor:{entry.name}",
                    confidence=AssessmentConfidence.MEDIUM,
                )
            )
            if entry.assessment_hint is not None:
                evidence.append(_hint_evidence(entry.assessment_hint, component=entry.name))
                hint_caps = _hint_capabilities(entry.assessment_hint)
                evidence.extend(self._capability_evidence(entry.name, hint_caps))

    async def _collect_scraper_evidence(
        self,
        client: httpx.AsyncClient,
        listing_url: str,
        listing_html: str,
        detail_urls: list[str],
        evidence: list[SourceEvidence],
    ) -> None:
        htmls: list[tuple[str, str]] = [(listing_url, listing_html)]
        for url in detail_urls:
            try:
                response = await client.get(url, follow_redirects=True)
                if response.status_code == 200:
                    htmls.append((str(response.url), response.text[:_MAX_HTML_CHARS]))
            except Exception:
                continue

        for entry in get_all_scraper_entries():
            html_values = [html for _, html in htmls]
            can_handle_result: Any = None
            if entry.assessment_probe is not None:
                try:
                    output = await _call_assessment_probe(
                        entry.assessment_probe,
                        urls=[url for url, _ in htmls],
                        htmls=html_values,
                        client=client,
                    )
                    evidence.extend(
                        _evidence_from_probe_output(
                            output,
                            component=f"scraper:{entry.name}",
                        )
                    )
                except Exception:
                    pass

            if entry.can_handle is not None:
                try:
                    can_handle_result = entry.can_handle(html_values)
                except Exception:
                    can_handle_result = None
                if can_handle_result is not None:
                    evidence.append(
                        SourceEvidence(
                            kind="scraper_can_handle",
                            value=entry.name,
                            confidence=AssessmentConfidence.MEDIUM,
                            details={str(key): value for key, value in can_handle_result.items()}
                            if isinstance(can_handle_result, dict)
                            else {},
                        )
                    )

            should_run_factory = (
                can_handle_result is not None or entry.name in _STATIC_SCRAPER_PROBES
            )
            if not should_run_factory:
                continue

            for url, html in htmls:
                try:
                    payload = await entry.factory(
                        url,
                        {"prefetched_html": html},
                        client,
                    )
                except Exception:
                    continue
                if payload is None:
                    continue
                if getattr(payload, "date_posted", None):
                    evidence.append(
                        SourceEvidence(
                            kind="scraper_payload",
                            value=f"{entry.name}:date_posted",
                            confidence=AssessmentConfidence.MEDIUM,
                            details={"url": url},
                        )
                    )
                    break
                metadata = getattr(payload, "metadata", None) or {}
                extras = getattr(payload, "extras", None) or {}
                if _has_date_key(metadata) or _has_date_key(extras):
                    evidence.append(
                        SourceEvidence(
                            kind="scraper_payload",
                            value=f"{entry.name}:date_field",
                            confidence=AssessmentConfidence.MEDIUM,
                            details={"url": url},
                        )
                    )
                    break

    def _capability_evidence(
        self, component: str, capabilities: SourceCapabilities
    ) -> list[SourceEvidence]:
        evidence: list[SourceEvidence] = []
        if capabilities.has_publication_time or capabilities.has_update_time:
            evidence.append(
                SourceEvidence(
                    kind="capability_signal",
                    value=f"{component}:item_datetime",
                    confidence=AssessmentConfidence.HIGH,
                )
            )
        if capabilities.supports_ordered_head:
            evidence.append(
                SourceEvidence(
                    kind="capability_signal",
                    value=f"{component}:ordered_head",
                    confidence=AssessmentConfidence.HIGH,
                )
            )
        if capabilities.has_rss_or_sitemap_dates:
            evidence.append(
                SourceEvidence(
                    kind="capability_signal",
                    value=f"{component}:rss_or_sitemap_dates",
                    confidence=AssessmentConfidence.HIGH,
                )
            )
        return evidence

    def _capabilities_from_evidence(
        self, base: SourceCapabilities, evidence: list[SourceEvidence]
    ) -> SourceCapabilities:
        capabilities = base
        for item in evidence:
            value = item.value.lower()
            details = item.details
            update = SourceCapabilities(source_family=capabilities.source_family)
            if (
                item.kind in {"surface_signal", "scraper_payload", "capability_signal"}
                and ("datetime" in value or "date_" in value or "date_posted" in value)
            ) or _has_date_key(details):
                update = update.model_copy(
                    update={
                        "has_publication_time": True,
                        "has_update_time": True,
                        "has_stable_url": True,
                    }
                )
            if "ordered" in value or _has_order_key(details):
                update = update.model_copy(
                    update={"supports_ordered_head": True, "has_stable_url": True}
                )
            if "last_modified" in value or "etag" in value:
                update = update.model_copy(
                    update={
                        "has_change_validators": True,
                        "has_page_change_signal": True,
                        "has_stable_url": True,
                    }
                )
            if "rss" in value or "sitemap" in value:
                update = update.model_copy(
                    update={"has_rss_or_sitemap_dates": True, "has_stable_url": True}
                )
            if item.kind.endswith("_hint"):
                update = update.model_copy(update={"known_integration": True})
            capabilities = _merge_capabilities(capabilities, update)
        return capabilities

    def _merge_into(
        self, capabilities: SourceCapabilities, extra: SourceCapabilities
    ) -> SourceCapabilities:
        return _merge_capabilities(capabilities, extra)


__all__ = ["CareerSiteAssessmentEngine", "CareerSiteProbeResult"]
