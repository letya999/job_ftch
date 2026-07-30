"""Base protocols for site-specific career-site parsers.

A site parser is a registered object tied to a URL pattern. It may implement
any combination of:

- `parse(spec, client) -> AsyncIterator[RawItem]`: full custom extraction logic
  (used for SPAs and special cases like Yandex).
- `runtime_defaults(url) -> dict | None`: pure runtime defaults injected into
  a generic `CareerSiteSpec` (url_filter, render, wait, expand_links, etc.).
  Used for static sites that just need a more accurate URL filter.
- `parser_kind(url) -> str | None`: hint for `DeclarativeCareerSiteConfig.from_spec`
  to pick the right `CareerSiteConfig` (e.g. `greenhouse`) when the spec
  declares `parser_kind: auto`.

Any combination is allowed. The site_parsers registry resolves by URL prefix
through `domain_pattern`. Per ADR-033 there is no host-keyed `if/elif` in core
composition — adding a site = a new module under this package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


@dataclass(frozen=True)
class SiteRuntimeDefaults:
    """Runtime defaults returned by `SiteParser.runtime_defaults`."""

    url_filter: Any | None = None
    render: bool | None = None
    wait: str | None = None
    include_if_detail_page: bool | None = None
    expand_links: tuple[str, ...] | None = None
    extra: dict[str, Any] | None = None


@runtime_checkable
class SiteParser(Protocol):
    """Parse a known career site into RawItems and/or supply runtime defaults.

    Implementations self-register via `@register_site_parser` and are looked up
    by URL prefix through `resolve_site_parser(url)`.
    """

    domain_pattern: str
    has_custom_parse: bool  # True if `parse()` should be used, False = defaults-only
    supports_discover: bool = False  # True if `discover()` should be used for Phase 1
    supports_search: bool = False  # True if `build_search_urls()` is implemented
    # How the site accepts target-role keywords:
    #   "combined"    -> one URL, all keywords OR-joined in a single query
    #   "per_keyword" -> one URL per keyword (site cannot take a multi-term query)
    #   "none"        -> no keyword search
    search_mode: Literal["combined", "per_keyword", "none"] = "none"

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults | None:
        """Return runtime defaults for `url`, or None if no defaults apply."""
        ...

    def parser_kind(self, url: str) -> str | None:
        """Return a parser-kind hint, or None."""
        ...

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        """Yield RawItems discovered on this career site."""
        ...

    async def discover(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> list[str]:
        """Return detail-page URLs only (Phase 1)."""
        ...

    def build_search_urls(
        self,
        base_url: str,
        keywords: Sequence[str],
        *,
        limit: int | None = None,
    ) -> list[str]:
        """Turn target-role keywords into concrete search/listing URLs.

        Returns one URL when `search_mode == "combined"`, or one URL per keyword
        when `search_mode == "per_keyword"`. An empty list means no usable search
        URL could be built (caller should keep the original `base_url`).
        """
        ...


# Backward-compatible alias. Old name kept for code that imports it.
SiteSpecificParser = SiteParser
