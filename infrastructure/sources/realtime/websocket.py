"""WebSocketSource stub — Phase 21, RM-109, not yet implemented."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from application.registry import register_source_v2

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from application.contracts import AuthProvider
    from domain import QuarantinedRawItem, RawItem
    from domain.source_spec import WebSocketSourceSpec


class WebSocketSource:
    def __init__(self, spec: WebSocketSourceSpec, auth: AuthProvider) -> None:
        self.spec = spec
        self.auth = auth

    async def fetch(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        raise NotImplementedError("WebSocketSource is not yet implemented (Phase 21 RM-109).")
        yield  # async generator


@register_source_v2("websocket")
def _create_websocket_source(spec: Any, auth: AuthProvider) -> WebSocketSource:
    return WebSocketSource(spec, auth)
