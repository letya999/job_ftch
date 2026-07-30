"""Expand bare career-site sources into keyword-filtered search sources.

A tenant lists aggregator sources as plain listing URLs (e.g.
``https://geekjob.ru/vacancies``). Given the tenant's target roles, each source
whose resolved site parser advertises ``supports_search`` is rewritten into one
(combined) or several (per-keyword) concrete search URLs so ingest starts from a
pre-filtered result page instead of the whole board.

The transform is idempotent: a source whose URL already carries an explicit
search query (``?text=``, ``?q=`` …) is left untouched, so hand-tuned sources
keep their operator-authored queries.
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
    from job_ftch.application.registry import resolve_site_parser
    from job_ftch.domain.source_spec import CareerSiteSpec

    roles = [role for role in (target_roles or ()) if isinstance(role, str) and role.strip()]
    if not roles:
        return list(specs)

    expanded: list[SourceSpec] = []
    for spec in specs:
        if not isinstance(spec, CareerSiteSpec):
            expanded.append(spec)
            continue
        if _url_has_search_query(spec.url):
            expanded.append(spec)
            continue
        parser = resolve_site_parser(spec.url)
        if parser is None or not getattr(parser, "supports_search", False):
            # Tier-1: no dedicated search parser. Hand the keywords to the source
            # so it can detect a search form at runtime (career_site_source only
            # acts on this for sites without any site parser at all).
            expanded.append(_attach_search_keywords(spec, roles))
            continue
        try:
            urls = list(parser.build_search_urls(spec.url, roles, limit=spec.limit))
        except Exception as exc:  # noqa: BLE001 - never let expansion drop a source
            logger.warning("search_expansion_failed", url=spec.url, error=str(exc))
            expanded.append(spec)
            continue
        if not urls:
            expanded.append(spec)
            continue
        for index, url in enumerate(urls):
            expanded.append(_clone_with_search_url(spec, url, index, len(urls)))
    return expanded


def _url_has_search_query(url: str) -> bool:
    query = parse_qs(urlparse(url).query)
    return any(
        key in query for key in ("q", "query", "text", "search", "keyword", "keywords", "qs")
    )


def _attach_search_keywords(spec: SourceSpec, roles: list[str]) -> SourceSpec:
    monitor_config = dict(getattr(spec, "monitor_config", {}) or {})
    monitor_config["_search_keywords"] = list(roles)
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
