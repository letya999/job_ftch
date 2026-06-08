"""WebhookSource stub — Phase 21, RM-108, not yet implemented."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from application.registry import register_source_v2

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from application.contracts import AuthProvider
    from domain import QuarantinedRawItem, RawItem
    from domain.source_spec import WebhookSourceSpec


class WebhookSource:
    def __init__(self, spec: WebhookSourceSpec, auth: AuthProvider) -> None:
        self.spec = spec
        self.auth = auth

    async def fetch(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        raise NotImplementedError("WebhookSource is not yet implemented (Phase 21 RM-108).")
        yield  # async generator


@register_source_v2("webhook")
def _create_webhook_source(spec: Any, auth: AuthProvider) -> WebhookSource:
    return WebhookSource(spec, auth)
