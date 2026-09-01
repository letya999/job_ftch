"""Expand career-site sources after source assessment selects search behavior.

A tenant lists aggregator sources as plain listing URLs (e.g.
``https://geekjob.ru/vacancies``). Given the tenant's target roles, each source
whose resolved site parser has a verified search recipe is rewritten into one
(combined) or several (per-keyword) concrete search URLs so ingest starts from a
pre-filtered result page instead of the whole board.

The assessment is attached to each prepared source before this function runs.
Specific parser builders remain the fallback for callers without an assessment;
assessed but unverified candidates stay at their original listing URL.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

import structlog

if TYPE_CHECKING:
    from collections.abc import Sequence

    from job_ftch.domain.source_spec import SourceSpec

logger = structlog.get_logger(__name__)


def expand_career_site_specs(
    specs: Sequence[SourceSpec],
    target_roles: Sequence[str],
) -> list[SourceSpec]:
    """Return ``specs`` with searchable career sites expanded by ``target_roles``."""
    from job_ftch.application.registry import resolve_site_parser_for_spec
    from job_ftch.domain.source_spec import CareerSiteSpec

    roles = [role for role in (target_roles or ()) if isinstance(role, str) and role.strip()]
    if not roles:
        return list(specs)

    expanded: list[SourceSpec] = []
    for spec in specs:
        if not isinstance(spec, CareerSiteSpec):
            expanded.append(spec)
            continue
        if _url_has_search_query(spec.url) and spec.search_locked:
            expanded.append(_attach_search_keywords(spec, roles, base_url=spec.url))
            continue
        base_url = spec.url
        assessment = (getattr(spec, "monitor_config", {}) or {}).get("_search_assessment")
        assessed_executor = assessment.get("executor") if isinstance(assessment, dict) else None
        assessed_status = assessment.get("status") if isinstance(assessment, dict) else None
        if assessed_executor in {"generic_get", "generic_post", "generic_browser"}:
            expanded.append(_attach_search_keywords(spec, roles, base_url=base_url))
            continue
        if assessed_status and assessed_status != "verified":
            expanded.append(_attach_search_keywords(spec, roles, base_url=base_url))
            continue
        parser = resolve_site_parser_for_spec(spec)
        if parser is None or not getattr(parser, "supports_search", False):
            # Every career source carries the roles into runtime. A specific
            # parser may still be used after the generic search executor finds
            # a form or browser/API recipe.
            expanded.append(_attach_search_keywords(spec, roles, base_url=base_url))
            continue
        try:
            urls = list(parser.build_search_urls(spec.url, roles, limit=spec.limit))
        except Exception as exc:  # noqa: BLE001 - never let expansion drop a source
            logger.warning("search_expansion_failed", url=spec.url, error=str(exc))
            expanded.append(_attach_search_keywords(spec, roles, base_url=base_url))
            continue
        if not urls:
            expanded.append(_attach_search_keywords(spec, roles, base_url=base_url))
            continue
        for index, url in enumerate(urls):
            expanded.append(
                _clone_with_search_url(
                    _attach_search_keywords(spec, roles, base_url=base_url),
                    url,
                    index,
                    len(urls),
                )
            )
    return expanded


def _url_has_search_query(url: str) -> bool:
    query = parse_qs(urlparse(url).query)
    return any(
        key in query for key in ("q", "query", "text", "search", "keyword", "keywords", "qs")
    )


def _attach_search_keywords(
    spec: SourceSpec,
    roles: list[str],
    *,
    base_url: str | None = None,
) -> SourceSpec:
    monitor_config = dict(getattr(spec, "monitor_config", {}) or {})
    monitor_config["_search_keywords"] = list(roles)
    monitor_config["_search_base_url"] = base_url or getattr(spec, "url", "")
    return spec.model_copy(update={"monitor_config": monitor_config})


def _clone_with_search_url(
    spec: SourceSpec,
    url: str,
    index: int,
    total: int,
) -> SourceSpec:
    update: dict[str, object] = {"url": url}
    # A single combined URL simply replaces the bare listing and keeps the
    # original source_name (no ledger/dedup churn). A per-keyword fan-out needs
    # unique names so the publish ledger and processed_key dedup stay correct.
    if total > 1:
        base = getattr(spec, "source_name", None) or "career_site"
        update["source_name"] = f"{base}_kw{index + 1}"
    return spec.model_copy(update=update)
