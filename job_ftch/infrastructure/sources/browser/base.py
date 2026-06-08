"""Browser-driven source. Requires [browser] extras (playwright)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_source_v2

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.application.contracts import AuthProvider
    from job_ftch.domain import QuarantinedRawItem, RawItem
    from job_ftch.domain.source_spec import BrowserSourceSpec


class BrowserSource:
    def __init__(self, spec: BrowserSourceSpec, auth: AuthProvider) -> None:
        self.spec = spec
        self.auth = auth
        self.source_name = spec.source_name or str(spec.url)
        try:
            import playwright  # noqa: F401

            self._available = True
        except ImportError:
            self._available = False

    async def fetch(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        if not self._available:
            raise ImportError(
                "playwright is required for browser sources. "
                "Install with: uv add playwright --optional browser"
            )
        raise NotImplementedError("BrowserSource requires a registered parser plugin.")
        yield  # make this an async generator


@register_source_v2("browser")
def _create_browser_source(spec: Any, auth: AuthProvider) -> BrowserSource:
    return BrowserSource(spec, auth)
