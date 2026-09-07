from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from job_ftch.application.builder import build_delivery_targets
from job_ftch.config import Settings
from job_ftch.domain import DeliveryEnvelope
from job_ftch.infrastructure.delivery.webhook import WebhookDeliveryTarget


def test_webhook_configuration_fails_closed_without_secret() -> None:
    with pytest.raises(ValueError, match="webhook_secret is required"):
        build_delivery_targets(Settings(webhook_url="https://example.test/hook"), None)


def test_webhook_signs_exact_body_and_idempotency() -> None:
    target = WebhookDeliveryTarget("https://example.test/hook", "secret")
    envelope = DeliveryEnvelope(
        outbox_id="o1",
        idempotency_key="a" * 64,
        decision_version="v1",
        sink_name=target.target_id,
        observation_id="obs-1",
        payload={"title": "Engineer"},
    )

    async def run() -> None:
        request: httpx.Request | None = None

        async def handler(req: httpx.Request) -> httpx.Response:
            nonlocal request
            request = req
            return httpx.Response(200, request=req)

        with patch("job_ftch.infrastructure.delivery.webhook.httpx.AsyncClient") as client_type:
            client = AsyncMock()
            client.__aenter__.return_value = client
            client.__aexit__.return_value = False

            async def post(url: str, **kwargs: object) -> httpx.Response:
                return await handler(
                    httpx.Request(
                        "POST",
                        url,
                        content=kwargs["content"],  # type: ignore[arg-type]
                        headers=kwargs["headers"],  # type: ignore[arg-type]
                    )
                )

            client.post.side_effect = post
            client_type.return_value = client
            await target.deliver_envelope(envelope, _job())

        assert request is not None
        body = request.content
        timestamp = request.headers["X-Jobfetch-Timestamp"]
        expected = hmac.new(b"secret", f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
        assert request.headers["X-Jobfetch-Signature"] == f"sha256={expected}"
        assert request.headers["X-Jobfetch-Idempotency-Key"] == envelope.idempotency_key
        assert json.loads(body)["event"] == "job.upserted"

    import asyncio

    asyncio.run(run())


def _job() -> object:
    from job_ftch.domain import JobRecord, SourceKind

    return JobRecord(
        raw_item_id="raw-1",
        source_kind=SourceKind.DEBUG,
        source_name="debug",
        title="Engineer",
        company="Example",
    )
