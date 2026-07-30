"""Builtin lightweight freshness assessment adapters."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from job_ftch.application.registry import (
    get_monitor_assessment_hint,
    register_source_assessment_adapter,
    resolve_monitor_assessment_hint,
    resolve_site_parser_assessment_hint,
)
from job_ftch.domain.runtime_source import source_spec_locator
from job_ftch.domain.source_assessment import (
    AssessmentConfidence,
    FreshnessAssessment,
    SourceAssessmentResult,
    SourceCapabilities,
    SourceEvidence,
)
from job_ftch.infrastructure.source_assessment.career_site_probe import (
    CareerSiteAssessmentEngine,
    CareerSiteProbeResult,
)

if TYPE_CHECKING:
    from job_ftch.application.registry import SourceRegistryAssessmentHint
    from job_ftch.application.source_assessment import SourceAssessmentContext
    from job_ftch.domain.source_spec import SourceSpec


_CAREER_SITE_ASSESSMENT_TIMEOUT_SECONDS = 12.0


async def _assess_career_site_probe(spec: SourceSpec) -> CareerSiteProbeResult:
    try:
        return await asyncio.wait_for(
            CareerSiteAssessmentEngine().assess(spec),  # type: ignore[arg-type]
            timeout=_CAREER_SITE_ASSESSMENT_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        return CareerSiteProbeResult(
            capabilities=SourceCapabilities(
                source_family="generic_site",
                has_stable_url=bool(source_spec_locator(spec)),
            ),
            evidence=(
                SourceEvidence(
                    kind="probe_status",
                    value="career_site_assessment_failed",
                    confidence=AssessmentConfidence.LOW,
                    details={"error": type(exc).__name__},
                ),
            ),
            freshness=FreshnessAssessment(
                confidence=AssessmentConfidence.LOW,
                can_detect_freshness_without_snapshot=False,
                can_filter_since_yesterday=False,
                requires_full_snapshot=True,
                probe_failed=True,
                rationale="Career-site assessment probe failed or timed out before reliable freshness signals were found.",
            ),
        )


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


def _merge_evidence(
    base: tuple[SourceEvidence, ...], extra: tuple[SourceEvidence, ...]
) -> tuple[SourceEvidence, ...]:
    seen: set[tuple[str, str]] = set()
    merged: list[SourceEvidence] = []
    for item in (*base, *extra):
        key = (item.kind, item.value)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return tuple(merged)


def _augment_with_probe(
    result: SourceAssessmentResult,
    probe: CareerSiteProbeResult,
) -> SourceAssessmentResult:
    capabilities = result.capabilities.model_copy(
        update={
            "has_publication_time": result.capabilities.has_publication_time
            or probe.capabilities.has_publication_time,
            "has_update_time": result.capabilities.has_update_time
            or probe.capabilities.has_update_time,
            "has_stable_id": result.capabilities.has_stable_id or probe.capabilities.has_stable_id,
            "has_stable_url": result.capabilities.has_stable_url
            or probe.capabilities.has_stable_url,
            "supports_ordered_head": result.capabilities.supports_ordered_head
            or probe.capabilities.supports_ordered_head,
            "has_cursor_or_since_filter": result.capabilities.has_cursor_or_since_filter
            or probe.capabilities.has_cursor_or_since_filter,
            "has_change_validators": result.capabilities.has_change_validators
            or probe.capabilities.has_change_validators,
            "has_page_change_signal": result.capabilities.has_page_change_signal
            or probe.capabilities.has_page_change_signal,
            "has_rss_or_sitemap_dates": result.capabilities.has_rss_or_sitemap_dates
            or probe.capabilities.has_rss_or_sitemap_dates,
            "has_embedded_state": result.capabilities.has_embedded_state
            or probe.capabilities.has_embedded_state,
            "known_integration": result.capabilities.known_integration
            or probe.capabilities.known_integration,
            "source_family": (
                result.capabilities.source_family
                if result.capabilities.source_family != "unknown"
                else probe.capabilities.source_family
            ),
        }
    )

    freshness = result.freshness
    confidence = _max_confidence(freshness.confidence, probe.freshness.confidence)
    if probe.freshness.item_level_dates:
        freshness = freshness.model_copy(
            update={
                "confidence": confidence,
                "can_detect_freshness_without_snapshot": True,
                "can_filter_since_yesterday": True,
                "item_level_dates": True,
                "ordered_by_newest": (
                    freshness.ordered_by_newest or probe.freshness.ordered_by_newest
                ),
                "requires_full_snapshot": False,
                "probe_failed": freshness.probe_failed or probe.freshness.probe_failed,
                "probe_blocked": freshness.probe_blocked or probe.freshness.probe_blocked,
                "rationale": probe.freshness.rationale,
            }
        )
    elif (
        probe.freshness.ordered_by_newest
        or probe.freshness.can_filter_since_yesterday
        or probe.freshness.can_detect_freshness_without_snapshot
        and not freshness.can_detect_freshness_without_snapshot
    ):
        freshness = freshness.model_copy(
            update={
                "confidence": confidence,
                "can_detect_freshness_without_snapshot": (
                    freshness.can_detect_freshness_without_snapshot
                    or probe.freshness.can_detect_freshness_without_snapshot
                ),
                "can_filter_since_yesterday": (
                    freshness.can_filter_since_yesterday
                    or probe.freshness.can_filter_since_yesterday
                ),
                "ordered_by_newest": (
                    freshness.ordered_by_newest or probe.freshness.ordered_by_newest
                ),
                "page_level_change_only": (
                    freshness.page_level_change_only or probe.freshness.page_level_change_only
                ),
                "requires_full_snapshot": (
                    freshness.requires_full_snapshot and probe.freshness.requires_full_snapshot
                ),
                "probe_failed": freshness.probe_failed or probe.freshness.probe_failed,
                "probe_blocked": freshness.probe_blocked or probe.freshness.probe_blocked,
                "rationale": (
                    probe.freshness.rationale
                    if probe.freshness.can_detect_freshness_without_snapshot
                    else freshness.rationale
                ),
            }
        )
    else:
        freshness = freshness.model_copy(
            update={
                "confidence": confidence,
                "probe_failed": freshness.probe_failed or probe.freshness.probe_failed,
                "probe_blocked": freshness.probe_blocked or probe.freshness.probe_blocked,
            }
        )

    return result.model_copy(
        update={
            "capabilities": capabilities,
            "evidence": _merge_evidence(result.evidence, probe.evidence),
            "freshness": freshness,
        }
    )


def _result(
    spec: SourceSpec,
    context: SourceAssessmentContext,
    *,
    capabilities: SourceCapabilities,
    evidence: tuple[SourceEvidence, ...],
    freshness: FreshnessAssessment,
) -> SourceAssessmentResult:
    return SourceAssessmentResult(
        source_id=context.source_id,
        source_type=spec.type,
        capabilities=capabilities,
        evidence=evidence,
        freshness=freshness,
    )


def _result_from_hint(
    spec: SourceSpec,
    context: SourceAssessmentContext,
    hint: SourceRegistryAssessmentHint,
) -> SourceAssessmentResult:
    return _result(
        spec,
        context,
        capabilities=SourceCapabilities(
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
        ),
        evidence=(
            SourceEvidence(
                kind=hint.evidence_kind,
                value=hint.evidence_value,
                confidence=hint.confidence,
            ),
        ),
        freshness=hint.freshness
        or FreshnessAssessment(
            confidence=hint.confidence,
            can_detect_freshness_without_snapshot=False,
            can_filter_since_yesterday=False,
            requires_full_snapshot=True,
            rationale=hint.rationale,
        ),
    )


class TelegramSourceAssessmentAdapter:
    name = "telegram"
    priority = 10

    def supports(self, spec: SourceSpec) -> bool:
        return spec.type.startswith("telegram")

    async def assess(
        self, spec: SourceSpec, context: SourceAssessmentContext
    ) -> SourceAssessmentResult:
        return _result(
            spec,
            context,
            capabilities=SourceCapabilities(
                source_family="telegram",
                has_publication_time=True,
                has_stable_id=True,
                has_stable_url=True,
                supports_ordered_head=True,
                known_integration=True,
            ),
            evidence=(
                SourceEvidence(
                    kind="native_timestamp",
                    value="message.date",
                    confidence=AssessmentConfidence.HIGH,
                ),
                SourceEvidence(
                    kind="native_identity",
                    value="message.id",
                    confidence=AssessmentConfidence.HIGH,
                ),
            ),
            freshness=FreshnessAssessment(
                confidence=AssessmentConfidence.HIGH,
                can_detect_freshness_without_snapshot=True,
                can_filter_since_yesterday=True,
                item_level_dates=True,
                ordered_by_newest=True,
                requires_full_snapshot=False,
                rationale="Telegram messages carry item-level publication timestamps and stable IDs.",
            ),
        )


class RSSSourceAssessmentAdapter:
    name = "rss"
    priority = 20

    def supports(self, spec: SourceSpec) -> bool:
        return spec.type == "rss_feed"

    async def assess(
        self, spec: SourceSpec, context: SourceAssessmentContext
    ) -> SourceAssessmentResult:
        return _result(
            spec,
            context,
            capabilities=SourceCapabilities(
                source_family="rss",
                has_publication_time=True,
                has_update_time=True,
                has_stable_id=True,
                has_stable_url=True,
                has_rss_or_sitemap_dates=True,
                known_integration=True,
            ),
            evidence=(
                SourceEvidence(
                    kind="feed_protocol",
                    value="rss_atom_entries",
                    confidence=AssessmentConfidence.HIGH,
                    details={"date_fields": ["published", "updated", "published_parsed"]},
                ),
            ),
            freshness=FreshnessAssessment(
                confidence=AssessmentConfidence.HIGH,
                can_detect_freshness_without_snapshot=True,
                can_filter_since_yesterday=True,
                item_level_dates=True,
                requires_full_snapshot=False,
                rationale="RSS/Atom entries usually expose per-item published or updated timestamps.",
            ),
        )


class KnownSourceAssessmentAdapter:
    name = "known"
    priority = 30

    _KNOWN_API_TYPES = frozenset({"hh_api", "greenhouse_api", "lever"})
    _KNOWN_API_HOST_TOKENS = ("api.hh.ru", "api.greenhouse.io", "boards-api.greenhouse.io")

    @staticmethod
    def _explicit_monitor(spec: SourceSpec) -> str | None:
        monitor = getattr(spec, "monitor", None)
        if not isinstance(monitor, str):
            return None
        normalized = monitor.strip()
        if not normalized or normalized == "auto":
            return None
        return normalized

    def supports(self, spec: SourceSpec) -> bool:
        if spec.type in self._KNOWN_API_TYPES:
            return True
        locator = source_spec_locator(spec) or ""
        host = (urlsplit(locator).hostname or locator).casefold()
        if any(token in host for token in self._KNOWN_API_HOST_TOKENS):
            return True
        monitor = self._explicit_monitor(spec)
        if monitor is not None and get_monitor_assessment_hint(monitor) is not None:
            return True
        if spec.type == "career_site":
            return (
                resolve_site_parser_assessment_hint(str(getattr(spec, "url", ""))) is not None
                or resolve_monitor_assessment_hint(locator) is not None
            )
        return False

    async def assess(
        self, spec: SourceSpec, context: SourceAssessmentContext
    ) -> SourceAssessmentResult:
        probe = await _assess_career_site_probe(spec) if spec.type == "career_site" else None

        if spec.type in self._KNOWN_API_TYPES or spec.type == "rest_api":
            return _result(
                spec,
                context,
                capabilities=SourceCapabilities(
                    source_family="known_api",
                    has_publication_time=True,
                    has_update_time=True,
                    has_stable_id=True,
                    has_stable_url=True,
                    has_cursor_or_since_filter=True,
                    known_integration=True,
                ),
                evidence=(
                    SourceEvidence(
                        kind="known_integration",
                        value=spec.type,
                        confidence=AssessmentConfidence.HIGH,
                    ),
                ),
                freshness=FreshnessAssessment(
                    confidence=AssessmentConfidence.HIGH,
                    can_detect_freshness_without_snapshot=True,
                    can_filter_since_yesterday=True,
                    item_level_dates=True,
                    requires_full_snapshot=False,
                    rationale="Known API integrations expose structured item metadata and incremental fields.",
                ),
            )

        monitor = self._explicit_monitor(spec)
        if monitor:
            hint = get_monitor_assessment_hint(monitor)
            if hint is not None:
                result = _result_from_hint(spec, context, hint)
                return _augment_with_probe(result, probe) if probe is not None else result

        if spec.type == "career_site":
            parser_hint = resolve_site_parser_assessment_hint(str(getattr(spec, "url", "")))
            if parser_hint is not None:
                result = _result_from_hint(spec, context, parser_hint)
                return _augment_with_probe(result, probe) if probe is not None else result

        hint = resolve_monitor_assessment_hint(source_spec_locator(spec) or "")
        if hint is not None:
            result = _result_from_hint(spec, context, hint)
            return _augment_with_probe(result, probe) if probe is not None else result

        msg = f"No registry freshness hint found for known source: {spec.type}"
        raise ValueError(msg)


class GenericSourceAssessmentAdapter:
    name = "generic"
    priority = 1000

    def supports(self, spec: SourceSpec) -> bool:
        return True

    async def assess(
        self, spec: SourceSpec, context: SourceAssessmentContext
    ) -> SourceAssessmentResult:
        probe = await _assess_career_site_probe(spec) if spec.type == "career_site" else None
        monitor = getattr(spec, "monitor", None)
        monitor_config = getattr(spec, "monitor_config", {}) or {}
        has_sitemap = monitor == "sitemap" or bool(monitor_config.get("sitemap_url"))
        has_url_filter = bool(getattr(spec, "url_filter", None) or monitor_config.get("url_filter"))
        supports_head = spec.type in {"career_site", "browser", "declarative_html", "rest_api"}

        if has_sitemap:
            result = _result(
                spec,
                context,
                capabilities=SourceCapabilities(
                    source_family="generic_site",
                    has_stable_url=True,
                    supports_ordered_head=has_url_filter,
                    has_rss_or_sitemap_dates=True,
                ),
                evidence=(
                    SourceEvidence(
                        kind="sitemap_hint",
                        value=(
                            "monitor_config.sitemap_url"
                            if monitor_config.get("sitemap_url")
                            else "monitor=sitemap"
                        ),
                        confidence=AssessmentConfidence.MEDIUM,
                    ),
                ),
                freshness=FreshnessAssessment(
                    confidence=AssessmentConfidence.MEDIUM,
                    can_detect_freshness_without_snapshot=True,
                    can_filter_since_yesterday=False,
                    page_level_change_only=True,
                    requires_full_snapshot=False,
                    rationale="Sitemap or feed-like metadata can indicate page freshness without full inventory snapshot.",
                ),
            )
            return _augment_with_probe(result, probe) if probe is not None else result

        if supports_head:
            result = _result(
                spec,
                context,
                capabilities=SourceCapabilities(
                    source_family="generic_site",
                    has_stable_url=True,
                    supports_ordered_head=has_url_filter,
                ),
                evidence=(
                    SourceEvidence(
                        kind="source_shape",
                        value="url_inventory",
                        confidence=AssessmentConfidence.MEDIUM,
                    ),
                ),
                freshness=FreshnessAssessment(
                    confidence=AssessmentConfidence.MEDIUM,
                    can_detect_freshness_without_snapshot=has_url_filter,
                    can_filter_since_yesterday=False,
                    ordered_by_newest=has_url_filter,
                    requires_full_snapshot=not has_url_filter,
                    rationale=(
                        "Generic site exposes URL-shaped inventory, but item-level freshness is unproven."
                    ),
                ),
            )
            return _augment_with_probe(result, probe) if probe is not None else result

        result = _result(
            spec,
            context,
            capabilities=SourceCapabilities(
                source_family="unknown",
                has_stable_url=bool(source_spec_locator(spec)),
                has_change_validators=True,
            ),
            evidence=(
                SourceEvidence(
                    kind="fallback",
                    value="unknown_source_shape",
                    confidence=AssessmentConfidence.LOW,
                ),
            ),
            freshness=FreshnessAssessment(
                confidence=AssessmentConfidence.LOW,
                can_detect_freshness_without_snapshot=False,
                can_filter_since_yesterday=False,
                requires_full_snapshot=True,
                rationale="No reliable freshness signal is visible from the current source specification.",
            ),
        )
        return _augment_with_probe(result, probe) if probe is not None else result


register_source_assessment_adapter("telegram", TelegramSourceAssessmentAdapter())
register_source_assessment_adapter("rss", RSSSourceAssessmentAdapter())
register_source_assessment_adapter("known", KnownSourceAssessmentAdapter())
register_source_assessment_adapter("generic", GenericSourceAssessmentAdapter())
