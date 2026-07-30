"""Recovery use case for durable outbox records."""

from __future__ import annotations

from collections.abc import Awaitable, Callable  # noqa: TC003
from typing import Any

import structlog

from job_ftch.domain import OutboxRecord  # noqa: TC001


async def recover_pending_outbox(
    store: Any,
    deliver: Callable[[OutboxRecord], Awaitable[None]],
    *,
    limit: int = 100,
    claim: Callable[[OutboxRecord], Awaitable[bool]] | None = None,
    release: Callable[[OutboxRecord], Awaitable[None]] | None = None,
) -> int:
    """Retry pending records; leave failures OUTBOXED for a later attempt."""
    logger = structlog.get_logger("job_ftch.outbox")
    delivered = 0
    for record in await store.list_pending_outbox(limit):
        if claim is not None and not await claim(record):
            continue
        try:
            await deliver(record)
        except Exception:  # caller owns retry/backoff policy
            logger.exception(
                "outbox_recovery_delivery_failed",
                idempotency_key=record.idempotency_key,
                sink_name=record.sink_name,
            )
            if release is not None:
                await release(record)
            continue
        await store.mark_outbox_delivered(record.idempotency_key)
        if release is not None:
            await release(record)
        delivered += 1
    return delivered
