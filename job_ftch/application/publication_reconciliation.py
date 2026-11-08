"""Operator access to unresolved publication attempts."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from job_ftch.application.publish_ledger import (
    _load_string_list,
    _persist_string_list,
    load_publish_ledger,
    persist_publish_ledger,
)
from job_ftch.domain import OutboxState


async def list_uncertain_publications(store: Any) -> list[dict[str, object]]:
    rows = []
    seen = set()
    for record in await store.list_uncertain_outbox(limit=10_000):
        if not record.sink_name.startswith("telegram:"):
            continue
        identity = (record.sink_name, record.delivery_payload["attempt_id"])
        if identity in seen:
            continue
        seen.add(identity)
        rows.append(
            {
                "id": record.idempotency_key,
                "job_id": record.delivery_payload["job_id"],
                "target": record.delivery_payload["target"],
            }
        )
    return rows


async def mark_verified_unsent(store: Any, record_id: str) -> None:
    records = await store.list_uncertain_outbox(limit=10_000)
    record = next(
        (
            item
            for item in records
            if item.idempotency_key == record_id and item.sink_name.startswith("telegram:")
        ),
        None,
    )
    if record is None:
        raise ValueError("uncertain publication not found")
    if not record.delivery_payload.get("attempt_id"):
        raise ValueError("uncertain publication has no attempt identity")
    peers = await store.list_outbox_attempt(record.delivery_payload["attempt_id"], record.sink_name)
    if any(item.state is OutboxState.DELIVERED for item in peers):
        raise ValueError("a related publication is already confirmed")
    for item in peers:
        if item.state is OutboxState.UNCERTAIN:
            await store.release_uncertain_outbox(item)


async def mark_verified_delivered(
    store: Any, record_id: str, *, chat_id: str, message_id: int
) -> None:
    if isinstance(message_id, bool) or message_id <= 0 or not chat_id.strip():
        raise ValueError("a chat id and positive message id are required")
    records = await store.list_uncertain_outbox(limit=10_000)
    record = next(
        (
            item
            for item in records
            if item.idempotency_key == record_id and item.sink_name.startswith("telegram:")
        ),
        None,
    )
    if record is None:
        raise ValueError("uncertain publication not found")
    if not record.delivery_payload.get("attempt_id"):
        raise ValueError("uncertain publication has no attempt identity")
    peers = await store.list_outbox_attempt(record.delivery_payload["attempt_id"], record.sink_name)
    for peer in peers:
        if peer.state is OutboxState.DELIVERED:
            prior = peer.delivery_payload.get("receipt")
            if (
                not isinstance(prior, dict)
                or str(prior.get("chat_id")) != chat_id
                or str(prior.get("message_id")) != str(message_id)
            ):
                raise ValueError("conflicting delivery receipt")
    receipt = {
        "chat_id": chat_id,
        "message_id": message_id,
        "confirmed_at": datetime.now(UTC).isoformat(),
    }
    for item in peers:
        if (
            item.state is OutboxState.UNCERTAIN
            and await store.confirm_uncertain_outbox(item, receipt) is None
        ):
            raise ValueError("uncertain publication changed during reconciliation")
    job_id = record.delivery_payload["job_id"]
    target = str(record.delivery_payload["target"])
    if job_id is not None:
        ledger = await load_publish_ledger(store)
        await persist_publish_ledger(store, [*ledger, job_id])
        key = f"bot_publish:target:{sha256(target.encode()).hexdigest()}:ids"
        target_ids = await _load_string_list(store, key)
        await _persist_string_list(store, key, [*target_ids, job_id])
