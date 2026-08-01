"""Durable delivery intent independent of any concrete sink."""

from __future__ import annotations

from enum import StrEnum
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field


class OutboxState(StrEnum):
    DECIDED = "decided"
    OUTBOXED = "outboxed"
    DELIVERED = "delivered"


def delivery_idempotency_key(*, content_hash: str, decision_version: str, sink_name: str) -> str:
    payload = f"{content_hash}|{decision_version}|{sink_name}".encode()
    return sha256(payload).hexdigest()


class OutboxRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outbox_id: str = Field(min_length=1)
    tenant_id: str = "default"
    observation_id: str = Field(min_length=1)
    content_hash: str = Field(min_length=64, max_length=64)
    decision_version: str = Field(min_length=1)
    sink_name: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=64, max_length=64)
    delivery_payload: dict[str, object] = Field(default_factory=dict)
    state: OutboxState = OutboxState.DECIDED


class DeliveryEnvelope(BaseModel):
    """Immutable delivery context passed to idempotency-aware destinations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outbox_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=64, max_length=64)
    decision_version: str = Field(min_length=1)
    sink_name: str = Field(min_length=1)
    observation_id: str = Field(min_length=1)
    payload: dict[str, object] = Field(default_factory=dict)


def delivery_envelope_from_record(record: OutboxRecord) -> DeliveryEnvelope:
    return DeliveryEnvelope(
        outbox_id=record.outbox_id,
        idempotency_key=record.idempotency_key,
        decision_version=record.decision_version,
        sink_name=record.sink_name,
        observation_id=record.observation_id,
        payload=record.delivery_payload,
    )
