"""Signed HTTP delivery target backed by the pipeline outbox."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from job_ftch.domain import DeliveryEnvelope, JobRecord


class WebhookDeliveryTarget:
    def __init__(self, url: str, secret: str, *, timeout_seconds: float = 10.0) -> None:
        if not url.startswith(("https://", "http://")):
            raise ValueError("webhook_url must use http or https")
        if not secret:
            raise ValueError("webhook secret must not be blank")
        self._url = url
        self._secret = secret.encode()
        self._timeout = timeout_seconds
        self._target_id = f"webhook:{hashlib.sha256(url.encode()).hexdigest()[:16]}"

    @property
    def target_id(self) -> str:
        return self._target_id

    async def deliver(self, item: JobRecord) -> None:
        payload = item.model_dump(mode="json")
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        await self._post(body, idempotency_key=hashlib.sha256(body).hexdigest())

    async def deliver_envelope(self, envelope: DeliveryEnvelope, item: JobRecord) -> None:
        body = json.dumps(
            {
                "version": "job_ftch.webhook.v1",
                "event": "job.upserted",
                "event_id": envelope.idempotency_key,
                "idempotency_key": envelope.idempotency_key,
                "observation_id": envelope.observation_id,
                "decision_version": envelope.decision_version,
                "emitted_at": time.time(),
                "job": item.model_dump(mode="json"),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        await self._post(body, idempotency_key=envelope.idempotency_key)

    async def _post(self, body: bytes, *, idempotency_key: str) -> None:
        timestamp = str(int(time.time()))
        signed = f"{timestamp}.".encode() + body
        signature = hmac.new(self._secret, signed, hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-Jobfetch-Timestamp": timestamp,
            "X-Jobfetch-Idempotency-Key": idempotency_key,
            "X-Jobfetch-Signature": f"sha256={signature}",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._url, content=body, headers=headers)
            response.raise_for_status()
