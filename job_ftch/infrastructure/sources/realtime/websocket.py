import asyncio
from collections.abc import AsyncIterator
from typing import Any

import structlog

from job_ftch.application.contracts import AuthProvider
from job_ftch.application.registry import register_source_spec
from job_ftch.domain import QuarantinedRawItem, RawItem, SourceKind
from job_ftch.domain.source_spec import WebSocketSourceSpec

try:
    import websockets
    from websockets.exceptions import ConnectionClosed

    _WEBSOCKETS_AVAILABLE = True
except ImportError:
    _WEBSOCKETS_AVAILABLE = False

logger = structlog.get_logger(__name__)


class WebSocketSource:
    """Persistent WebSocket client with exponential backoff reconnection."""

    _MAX_BACKOFF = 300.0  # 5 minutes max backoff

    def __init__(self, spec: WebSocketSourceSpec, auth: AuthProvider) -> None:
        if not _WEBSOCKETS_AVAILABLE:
            raise ImportError(
                "websockets is required for WebSocketSource. Run: pip install 'job_ftch[realtime]'"
            )
        self.spec = spec
        self.auth = auth
        self._stop = asyncio.Event()

    async def fetch(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        backoff = 1.0
        source_name = self.spec.source_name or "websocket"

        while not self._stop.is_set():
            try:
                async with websockets.connect(self.spec.url) as ws:
                    backoff = 1.0  # reset on successful connection
                    logger.info("websocket_source_connected", url=self.spec.url)

                    async for message in ws:
                        if self._stop.is_set():
                            return
                        text = (
                            message
                            if isinstance(message, str)
                            else message.decode("utf-8", errors="replace")
                        )
                        if not text.strip():
                            continue
                        yield RawItem(
                            source_kind=SourceKind.CAREER_SITE,
                            source_name=source_name,
                            external_id=str(id(message)),
                            text=text.strip(),
                        )

            except ConnectionClosed:
                logger.warning("websocket_source_disconnected", url=self.spec.url, backoff=backoff)
            except Exception as exc:
                logger.error("websocket_source_error", url=self.spec.url, error=str(exc))

            if self._stop.is_set():
                return
            await asyncio.sleep(min(backoff, self._MAX_BACKOFF))
            backoff = min(backoff * 2, self._MAX_BACKOFF)

    def stop(self) -> None:
        self._stop.set()


@register_source_spec("websocket")
def _create_websocket(spec: Any, auth: AuthProvider, store: Any = None) -> WebSocketSource:
    return WebSocketSource(spec, auth)
