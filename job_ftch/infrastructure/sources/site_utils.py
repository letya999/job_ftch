from __future__ import annotations

import re
from datetime import UTC
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from job_ftch.domain import (
    AcquisitionTransport,
    ObservationKind,
    SourceFamily,
    SourceIdentity,
)
from job_ftch.domain.models import SourceKind
from job_ftch.domain.site_models import (
    DiscoveredPostingPayload,
    MonitorResult,
    ScrapedPostingPayload,
)
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.source_policy import source_policy_metadata

if TYPE_CHECKING:
    from collections.abc import Callable

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


def normalize_monitor_result(raw: Any) -> MonitorResult:
    """Normalize various return types from monitors into MonitorResult."""
    if isinstance(raw, MonitorResult):
        return raw
    if isinstance(raw, list):
        # List of payloads
        payloads = {p.url: p for p in raw if hasattr(p, "url")}
        return MonitorResult(urls=set(payloads.keys()), payloads_by_url=payloads)
    if isinstance(raw, set):
        return MonitorResult(urls=raw)
    if isinstance(raw, tuple) and len(raw) == 2 and isinstance(raw[0], set):
        # (URLs, discovered sitemap URL)
        return MonitorResult(
            urls=raw[0],
            metadata_updates={"discovered_url": raw[1]} if raw[1] else {},
        )
    return MonitorResult()


def apply_url_filter(
    result: MonitorResult, url_filter_config: str | dict[str, Any] | list[str] | None
) -> MonitorResult:
    """Apply include/exclude/suffix/prefix filters to discovered URLs.

    Delegates to :class:`URLFilterChain`. A bare string is treated as an
    ``include`` regex; a dict supports ``include``/``exclude``/``suffix``/
    ``prefix``; a list is a set of ``include`` regexes.
    """
    if not url_filter_config:
        return result

    normalized: dict[str, Any] | list[str]
    if isinstance(url_filter_config, str):
        normalized = {"include": url_filter_config}
    else:
        normalized = url_filter_config

    chain = build_filter_chain_from_config(normalized)
    if chain is None:
        return result
    return chain.apply(result)


def apply_url_transform(
    result: MonitorResult, url_transform_config: dict[str, Any] | None
) -> MonitorResult:
    """Apply regex find/replace transforms to discovered URLs."""
    if not url_transform_config:
        return result

    find = url_transform_config.get("find")
    replace = url_transform_config.get("replace", "")
    if not find:
        return result

    transformed_urls: set[str] = set()
    transformed_payloads: dict[str, DiscoveredPostingPayload] = {}

    for url in result.urls:
        new_url = re.sub(str(find), str(replace), url)
        transformed_urls.add(new_url)
        if url in result.payloads_by_url:
            payload = result.payloads_by_url[url]
            import dataclasses

            new_payload = dataclasses.replace(payload, url=new_url)
            transformed_payloads[new_url] = new_payload

    result.urls = transformed_urls
    result.payloads_by_url = transformed_payloads
    return result


# ---------------------------------------------------------------------------
# Composable URL filter chain (design pattern from Botasaurus FiltersAndExtractors)
# ---------------------------------------------------------------------------


class URLFilterChain:
    r"""Composable, chainable URL filter inspired by Botasaurus FiltersAndExtractors.

    Usage::

        chain = (
            URLFilterChain()
            .include(r"/jobs/")
            .exclude(r"\.(pdf|zip)$")
            .require_suffix(".html")
            .custom(lambda url: "apply" not in url)
        )
        filtered = chain.apply(monitor_result)
    """

    def __init__(self) -> None:
        self._predicates: list[Callable[[str], bool]] = []

    def include(self, pattern: str) -> URLFilterChain:
        """Keep URLs matching the regex *pattern*."""
        compiled = re.compile(pattern)
        self._predicates.append(lambda url: bool(compiled.search(url)))
        return self

    def include_any(self, *patterns: str) -> URLFilterChain:
        """Keep URLs matching ANY of the regex *patterns* (OR semantics)."""
        compiled = [re.compile(p) for p in patterns if p]
        if not compiled:
            return self
        self._predicates.append(lambda url: any(c.search(url) for c in compiled))
        return self

    def exclude(self, pattern: str) -> URLFilterChain:
        """Drop URLs matching the regex *pattern*."""
        compiled = re.compile(pattern)
        self._predicates.append(lambda url: not compiled.search(url))
        return self

    def require_suffix(self, *suffixes: str) -> URLFilterChain:
        """Keep URLs whose path ends with one of *suffixes*."""
        self._predicates.append(
            lambda url: urlparse(url).path.lower().endswith(tuple(s.lower() for s in suffixes))
        )
        return self

    def require_prefix(self, *prefixes: str) -> URLFilterChain:
        """Keep URLs whose path starts with one of *prefixes*."""
        self._predicates.append(lambda url: urlparse(url).path.startswith(tuple(prefixes)))
        return self

    def custom(self, predicate: Callable[[str], bool]) -> URLFilterChain:
        """Add an arbitrary predicate filter."""
        self._predicates.append(predicate)
        return self

    def matches(self, url: str) -> bool:
        """Return True if *url* passes all predicates."""
        return all(p(url) for p in self._predicates)

    def apply(self, result: MonitorResult) -> MonitorResult:
        """Apply all predicates to a MonitorResult, returning a new filtered result."""
        filtered_urls: set[str] = set()
        filtered_payloads: dict[str, DiscoveredPostingPayload] = {}
        removed = 0

        for url in result.urls:
            if self.matches(url):
                filtered_urls.add(url)
                if url in result.payloads_by_url:
                    filtered_payloads[url] = result.payloads_by_url[url]
            else:
                removed += 1

        result.urls = filtered_urls
        result.payloads_by_url = filtered_payloads
        result.filtered_count += removed
        return result


def build_filter_chain_from_config(
    config: dict[str, Any] | list[str] | None,
) -> URLFilterChain | None:
    """Build a URLFilterChain from a config dict or list of regex patterns.

    Config dict keys: ``include``, ``exclude`` (each a regex string or list of
    strings), plus ``suffix``/``prefix``. Multiple include patterns use OR
    semantics (keep if any match). List of strings: treated as OR-include.
    """
    if not config:
        return None

    chain = URLFilterChain()

    if isinstance(config, list):
        chain.include_any(*(p for p in config if p))
        return chain

    if isinstance(config, dict):
        includes = config.get("include")
        excludes = config.get("exclude")
        suffixes = config.get("suffix")
        prefixes = config.get("prefix")

        if isinstance(includes, str):
            includes = [includes]
        if includes:
            chain.include_any(*(p for p in includes if p))

        if isinstance(excludes, str):
            excludes = [excludes]
        for pat in excludes or []:
            if pat:
                chain.exclude(pat)

        if isinstance(suffixes, str):
            suffixes = [suffixes]
        if suffixes:
            chain.require_suffix(*suffixes)

        if isinstance(prefixes, str):
            prefixes = [prefixes]
        if prefixes:
            chain.require_prefix(*prefixes)

    return chain


def enrich_description(
    payload: DiscoveredPostingPayload | ScrapedPostingPayload,
) -> str | None:
    """Append extras (skills, responsibilities, etc.) to description HTML."""
    desc = payload.description or ""
    if not payload.extras:
        return payload.description or None

    extra_parts = []
    # Common keys ported from jobseek
    for key in (
        "responsibilities",
        "qualifications",
        "requirements",
        "skills",
        "benefits",
        "perks",
    ):
        if value := payload.extras.get(key):
            if isinstance(value, list):
                value = "<ul>" + "".join(f"<li>{v}</li>" for v in value) + "</ul>"
            extra_parts.append(f"<h3>{key.capitalize()}</h3>\n{value}")

    if extra_parts:
        if desc:
            desc += "\n<hr/>\n"
        desc += "\n\n".join(extra_parts)

    return desc or None


def payload_to_raw_item(
    payload: DiscoveredPostingPayload,
    spec: CareerSiteSpec,
    source_name: str,
) -> RawItem:
    """Convert infra-layer payload to domain-layer RawItem."""
    description = enrich_description(payload)
    title = payload.title or "Unknown Job"
    text = f"{title}\n\n{description}" if description else title

    meta: dict[str, Any] = {
        **source_policy_metadata(spec.url),
        **(payload.metadata or {}),
        **(payload.localizations or {}),
    }
    meta["title"] = title
    if payload.locations:
        meta["locations"] = payload.locations
    if payload.employment_type:
        meta["employment_type"] = payload.employment_type
    if payload.job_location_type:
        meta["job_location_type"] = payload.job_location_type
    if payload.date_posted:
        meta["date_posted"] = payload.date_posted
    if payload.base_salary:
        meta["base_salary"] = payload.base_salary
    if payload.language:
        meta["language"] = payload.language
    if payload.extras:
        meta["extras"] = payload.extras

    observation_kind = (
        ObservationKind.VACANCY_DETAIL
        if meta.get("detail_vacancy_confirmed") is True
        else ObservationKind.STRUCTURED_RECORD
    )
    meta.update(
        {
            "source_family": SourceFamily.CAREER_WEB.value,
            "observation_kind": observation_kind.value,
            "transport": AcquisitionTransport.HTTP.value,
            "adapter": str(meta.get("adapter") or "generic-career-site"),
            "parser_version": str(meta.get("parser_version") or "career-site-v2"),
        }
    )

    # Use the canonical URL as external_id — gives each career-site item a unique stable key
    # so downstream dedup and LLM cache keys don't collide (all None was the prior state).
    external_id = payload.url or None

    created_at = None
    if payload.date_posted:
        try:
            from datetime import datetime

            parsed_at = datetime.fromisoformat(payload.date_posted.replace("Z", "+00:00"))
            created_at = (
                parsed_at.replace(tzinfo=UTC)
                if parsed_at.tzinfo is None
                else parsed_at.astimezone(UTC)
            )
        except (ValueError, AttributeError):
            try:
                import calendar as _cal
                import email.utils as _eu

                _t = _eu.parsedate(payload.date_posted)
                if _t:
                    created_at = datetime.fromtimestamp(_cal.timegm(_t), tz=UTC)
            except Exception:
                created_at = None

    return build_raw_item(
        url=payload.url,
        text=text,
        source_name=source_name,
        source_kind=SourceKind.CAREER_SITE,
        external_id=external_id,
        metadata=meta,
        created_at=created_at,
        source_identity=SourceIdentity(
            family=SourceFamily.CAREER_WEB,
            observation_kind=observation_kind,
            transport=AcquisitionTransport.HTTP,
            adapter=meta["adapter"],
            parser_version=meta["parser_version"],
            legacy_kind=SourceKind.CAREER_SITE.value,
        ),
    )
