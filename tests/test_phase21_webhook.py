import pytest

from domain.source_spec import WebhookSourceSpec
from infrastructure.sources.realtime.webhook import WebhookSource, _payload_to_text


def test_webhook_source_requires_aiohttp(monkeypatch):
    import infrastructure.sources.realtime.webhook as webhook

    monkeypatch.setattr(webhook, "_AIOHTTP_AVAILABLE", False)

    spec = WebhookSourceSpec(path="/test")
    with pytest.raises(ImportError, match="aiohttp is required"):
        WebhookSource(spec, auth=None)  # type: ignore


def test_webhook_payload_to_text_extracts_text_field():
    assert _payload_to_text({"text": "hello"}) == "hello"
    assert _payload_to_text({"content": "world"}) == "world"
    assert _payload_to_text({"body": "body text"}) == "body text"
    assert _payload_to_text({"description": "desc"}) == "desc"
    assert _payload_to_text({"message": "msg"}) == "msg"


def test_webhook_payload_to_text_fallback_fields():
    # priority: text > content > body
    payload = {"text": "T", "content": "C", "body": "B"}
    assert _payload_to_text(payload) == "T"

    payload2 = {"content": "C", "body": "B"}
    assert _payload_to_text(payload2) == "C"


def test_webhook_source_spec_has_host_port():
    spec = WebhookSourceSpec(path="/x", host="127.0.0.1", port=9000)
    assert spec.host == "127.0.0.1"
    assert spec.port == 9000
    assert spec.path == "/x"
