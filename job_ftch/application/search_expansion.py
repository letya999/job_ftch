"""Expand career-site sources after source assessment selects search behavior.

A tenant lists aggregator sources as plain listing URLs (e.g.
``https://geekjob.ru/vacancies``). Target roles are first compacted into search
queries (slash-split, distinctive AI/LLM terms, length cap). Each source whose
resolved site parser advertises ``supports_search`` is rewritten into one
(combined) or a few (per-keyword, capped) concrete search URLs.

A failed HTML-form assessment does not suppress a deterministic parser builder:
GeekJob's JSON ``qs=`` and HH ``text=A OR B`` are different surfaces from
``detect_search_form``. Unverified sources without a parser search stay on the
original listing and still carry ``_search_keywords`` for the generic runtime
path.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

import structlog

if TYPE_CHECKING:
    from collections.abc import Sequence

    from job_ftch.domain.source_spec import SourceSpec

logger = structlog.get_logger(__name__)

# Per-keyword fan-out eats the 50-source run budget; keep it tiny.
_PER_KEYWORD_FANOUT_CAP = 3
_COMBINED_QUERY_CAP = 8
_DISTINCTIVE_ROLE = re.compile(
    r"\b(ai|llm|ml|mlops|genai|agent|machine learning)\b",
    re.IGNORECASE,
)


def search_queries_from_target_roles(
    target_roles: Sequence[str],
    *,
    cap: int = _COMBINED_QUERY_CAP,
) -> list[str]:
    """Turn profile target roles into concentrated search queries.

    Splits ``A / B`` aliases, drops generic leftovers when distinctive AI/LLM
    terms exist, and caps length so a combined ``OR`` query stays title-sized
    instead of matching every "developer" on the board.
    """
    seen: set[str] = set()
    distinctive: list[str] = []
    generic: list[str] = []
    for raw in target_roles or ():
        if not isinstance(raw, str):
            continue
        for chunk in re.split(r"\s*/\s*", raw):
            text = " ".join(chunk.split()).strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            if _DISTINCTIVE_ROLE.search(text):
                distinctive.append(text)
            else:
                generic.append(text)
    chosen = distinctive or generic
    return chosen[: max(cap, 0)]


def expand_career_site_specs(
    specs: Sequence[SourceSpec],
    target_roles: Sequence[str],
) -> list[SourceSpec]:
    """Return ``specs`` with searchable career sites expanded by ``target_roles``."""
    from job_ftch.application.registry import resolve_site_parser_for_spec
    from job_ftch.domain.source_spec import CareerSiteSpec

    roles = search_queries_from_target_roles(target_roles)
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
        assessed_status = assessment.get("status") if isinstance(assessment, dict) else None
        parser = resolve_site_parser_for_spec(spec)
        # Parser search wins over a generic HTML-form recipe: X5 ``search=``
        # and HH ``text=A OR B`` are different surfaces from ``detect_search_form``.
        # A verified generic_get must not suppress those builders.
        if parser is not None and getattr(parser, "supports_search", False):
            try:
                urls = list(parser.build_search_urls(spec.url, roles, limit=spec.limit))
            except Exception as exc:  # noqa: BLE001 - never let expansion drop a source
                logger.warning("search_expansion_failed", url=spec.url, error=str(exc))
                urls = []
            if urls:
                mode = str(getattr(parser, "search_mode", "") or "")
                if mode == "per_keyword" and len(urls) > _PER_KEYWORD_FANOUT_CAP:
                    urls = urls[:_PER_KEYWORD_FANOUT_CAP]
                for index, url in enumerate(urls):
                    expanded.append(
                        _clone_with_search_url(
                            _attach_search_keywords(spec, roles, base_url=base_url),
                            url,
                            index,
                            len(urls),
                        )
                    )
                continue
        if assessed_status and assessed_status != "verified":
            expanded.append(_attach_search_keywords(spec, roles, base_url=base_url))
            continue
        expanded.append(_attach_search_keywords(spec, roles, base_url=base_url))
    return expanded


def _url_has_search_query(url: str) -> bool:
    query = parse_qs(urlparse(url).query)
    return any(
        key in query
        for key in (
            "q",
            "query",
            "text",
            "search",
            "keyword",
            "keywords",
            "qs",
            "job_search",
            "roles",
        )
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
