import asyncio
from collections.abc import AsyncIterator
from typing import Any

import structlog

from job_ftch.application.contracts import AuthProvider
from job_ftch.application.registry import register_source_spec
from job_ftch.domain import (
    AcquisitionTransport,
    ObservationKind,
    QuarantinedRawItem,
    RawItem,
    SourceFamily,
    SourceIdentity,
    SourceKind,
)
from job_ftch.domain.source_spec import WebhookSourceSpec

try:
    from aiohttp import web

    _AIOHTTP_AVAILABLE = True
except ImportError:
    _AIOHTTP_AVAILABLE = False

logger = structlog.get_logger(__name__)


class WebhookSource:
    """Embedded aiohttp HTTP server. Yields RawItems from incoming POST requests."""

    def __init__(self, spec: WebhookSourceSpec, auth: AuthProvider) -> None:
        if not _AIOHTTP_AVAILABLE:
            raise ImportError(
                "aiohttp is required for WebhookSource. Run: pip install 'job_ftch[realtime]'"
            )
        self.spec = spec
        self.auth = auth
        self._queue: asyncio.Queue[RawItem] = asyncio.Queue()
        self._stop = asyncio.Event()

    async def fetch(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        app = web.Application()
        app.router.add_post(self.spec.path, self._handle)

        runner = web.AppRunner(app)
        await runner.setup()
        host = getattr(self.spec, "host", "0.0.0.0")  # nosec B104 - operator-configured
        port = getattr(self.spec, "port", 8080)
        site = web.TCPSite(runner, host, port)
        await site.start()
        logger.info("webhook_source_listening", path=self.spec.path, port=port)

        try:
            while not self._stop.is_set():
                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                    yield item
                except TimeoutError:
                    continue
        finally:
            await runner.cleanup()

    async def _handle(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.Response(status=400, text="Invalid JSON")

        source_name = self.spec.source_name or "webhook"
        text = _payload_to_text(payload)
        if not text:
            return web.Response(status=422, text="No text content found")
        event_id = _get_payload_path(payload, self.spec.event_id_field)
        if event_id in (None, ""):
            return web.Response(status=422, text="Stable event id is required")

        item = RawItem(
            source_kind=SourceKind.CAREER_SITE,
            source_identity=SourceIdentity(
                family=SourceFamily.REALTIME,
                observation_kind=ObservationKind.MESSAGE,
                transport=AcquisitionTransport.WEBHOOK,
                adapter="webhook",
                parser_version="webhook-v1",
                legacy_kind=str(SourceKind.CAREER_SITE),
            ),
            source_name=source_name,
            external_id=str(event_id),
            url=payload.get("url"),
            text=text,
            metadata={
                "raw": payload,
                "source_family": SourceFamily.REALTIME.value,
                "observation_kind": ObservationKind.MESSAGE.value,
            },
        )
        await self._queue.put(item)
        return web.Response(status=200, text="OK")

    def stop(self) -> None:
        self._stop.set()


def _payload_to_text(payload: dict[str, Any]) -> str:
    for key in ("text", "content", "body", "description", "message"):
        if isinstance(payload.get(key), str) and payload[key].strip():
            return str(payload[key]).strip()
    return ""


def _get_payload_path(payload: dict[str, Any], path: str) -> Any:
    value: Any = payload
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


@register_source_spec("webhook")
def _create_webhook(spec: Any, auth: AuthProvider, store: Any = None) -> WebhookSource:
    return WebhookSource(spec, auth)
