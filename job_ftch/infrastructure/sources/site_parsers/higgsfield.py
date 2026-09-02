"""Higgsfield careers is an Ashby board behind a custom domain redirect."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain.site_models import DiscoveredPostingPayload, MonitorResult
from job_ftch.infrastructure.sources.monitors.ashby import can_handle
from job_ftch.infrastructure.sources.monitors.ashby import discover as ashby_discover
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_utils import payload_to_raw_item

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

_ASHBY_HOSTS = ["careers.higgsfield.kz", "jobs.ashbyhq.com", "api.ashbyhq.com"]


def _payloads_from_ashby(
    result: MonitorResult | list[DiscoveredPostingPayload],
) -> list[DiscoveredPostingPayload]:
    if isinstance(result, list):
        return result
    if result.payloads_by_url:
        return list(result.payloads_by_url.values())
    return []


class HiggsfieldParser:
    """Emit Ashby posting-api payloads without scraping captcha-gated details."""

    domain_pattern = r"^https?://careers\.higgsfield\.kz(?:/|$)"
    has_custom_parse = True
    confirmed_empty_on_empty = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False,
            include_if_detail_page=False,
            extra={
                "captcha_authorized_domains": list(_ASHBY_HOSTS),
                "proxy_rescue_allow_domains": list(_ASHBY_HOSTS),
            },
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return "ashby"

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        monitor_config = dict(spec.monitor_config)
        detected = await can_handle(spec.url, client)
        token = (detected or {}).get("token")
        if token:
            monitor_config["token"] = token
        ashby_spec = spec.model_copy(update={"monitor_config": monitor_config})
        result = await ashby_discover(ashby_spec, client)
        if isinstance(result, MonitorResult) and result.metadata_updates.get("confirmed_empty"):
            return
        source_name = spec.source_name or "higgsfield"
        limit = spec.limit or 50
        for emitted, payload in enumerate(_payloads_from_ashby(result), start=1):
            metadata = dict(payload.metadata or {})
            metadata.setdefault("parser", "higgsfield_ashby")
            metadata.setdefault("detail_vacancy_confirmed", True)
            metadata.setdefault("company", "Higgsfield")
            metadata["company_authoritative"] = True
            payload.metadata = metadata
            yield payload_to_raw_item(payload, spec, source_name)
            if emitted >= limit:
                return


register_site_parser(
    "higgsfield",
    domain_pattern=HiggsfieldParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:careers.higgsfield.kz",
        has_stable_id=True,
        has_publication_time=True,
        can_detect_freshness_without_snapshot=True,
        item_level_dates=True,
        requires_full_snapshot=False,
        rationale="careers.higgsfield.kz redirects to an Ashby board with a public posting API.",
    ),
)(HiggsfieldParser)
