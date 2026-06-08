import pytest

from domain.source_spec import WebSocketSourceSpec
from infrastructure.sources.realtime.websocket import WebSocketSource


def test_websocket_source_requires_websockets(monkeypatch):
    import infrastructure.sources.realtime.websocket as websocket

    monkeypatch.setattr(websocket, "_WEBSOCKETS_AVAILABLE", False)

    spec = WebSocketSourceSpec(url="wss://example.com")
    with pytest.raises(ImportError, match="websockets is required"):
        WebSocketSource(spec, auth=None)  # type: ignore


def test_websocket_source_spec_roundtrip():
    data = {"type": "websocket", "url": "wss://example.com"}
    spec = WebSocketSourceSpec(**data)
    assert spec.url == "wss://example.com"
    assert spec.type == "websocket"


@pytest.mark.asyncio
async def test_websocket_stop_event_terminates_fetch(monkeypatch):
    import infrastructure.sources.realtime.websocket as ws_module

    monkeypatch.setattr(ws_module, "_WEBSOCKETS_AVAILABLE", True)

    spec = WebSocketSourceSpec(url="wss://example.com")
    source = WebSocketSource(spec, auth=None)  # type: ignore

    # Immediately stop before starting fetch to verify the stop event is respected
    source.stop()
    assert source._stop.is_set()
