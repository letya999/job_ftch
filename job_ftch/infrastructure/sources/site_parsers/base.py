"""Base protocol for site-specific career-site parsers.

These parsers bypass the generic monitor/scraper chain for sites that need
special handling (e.g. SPA with infinite scroll, internal API, unusual markup).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


@runtime_checkable
class SiteSpecificParser(Protocol):
    """Parse a known career site into RawItems."""

    domain_pattern: str

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        """Yield RawItems discovered on this career site."""
        ...
