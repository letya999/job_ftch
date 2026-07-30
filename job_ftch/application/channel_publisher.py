"""Single implementation of "deliver accepted vacancies to a target".

Both the manual `/run` path and the background scheduler used to carry their
own copy of this loop. The copies drifted: only the scheduler retried flood
waits and kept a retry window, only the chat path stopped the batch on a send
error. Delivery semantics now live here, and adapters supply nothing but a
transport.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import structlog

from job_ftch.application.publish_ledger import (
    extract_publish_job_id,
    load_publish_ledger,
    persist_publish_ledger,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from job_ftch.application.publish_ledger import RunStateStore
    from job_ftch.domain import Job

logger = structlog.get_logger(__name__)

# Telegram tolerates roughly one channel post per 3 seconds; the extra margin
# keeps a long batch from tripping flood control halfway through.
_DEFAULT_THROTTLE_SECONDS = 3.5
_DEFAULT_TRANSIENT_ATTEMPTS = 2

# Substrings that mean the target itself is unusable. Retrying or continuing
# the batch against such a target only burns rate limit.
_FATAL_TARGET_MARKERS = ("forbidden", "not a member", "kicked", "chat not found", "bot was blocked")


class TransientSendError(Exception):
    """Rate limiting: the same job may succeed after `retry_after` seconds."""

    def __init__(self, retry_after: float, detail: str = "") -> None:
        super().__init__(detail or f"retry after {retry_after}s")
        self.retry_after = float(retry_after)


class FatalTargetError(Exception):
    """The target cannot receive messages at all (blocked, kicked, deleted)."""


class CardSender(Protocol):
    """Transport for one rendered vacancy. Implemented by the delivery adapter."""

    async def send(self, target: str, job: Job) -> None: ...


@dataclass
class PublishOutcome:
    sent: int = 0
    skipped_already_published: int = 0
    error: str | None = None
    # True only for rate-limit exhaustion, so callers can decide whether to keep
    # a retry window open. A permanently bad card must never pin the window, or
    # every cycle re-sends the whole batch.
    had_transient_failure: bool = False
    target_unusable: bool = False
    delivered: list[Job] = field(default_factory=list)


def _is_fatal_target_error(error: BaseException) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in _FATAL_TARGET_MARKERS)


async def publish_jobs(
    jobs: Sequence[Job],
    *,
    target: str,
    sender: CardSender,
    store: RunStateStore | None = None,
    send_limit: int,
    throttle_seconds: float = _DEFAULT_THROTTLE_SECONDS,
    transient_attempts: int = _DEFAULT_TRANSIENT_ATTEMPTS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> PublishOutcome:
    """Send up to `send_limit` unpublished jobs to `target`.

    When `store` is provided the publish ledger is consulted and updated after
    each success, so a crash mid-batch cannot re-send what already landed.
    """
    outcome = PublishOutcome()
    if send_limit <= 0 or not jobs:
        return outcome

    ledger: list[str] = []
    published_ids: set[str] = set()
    if store is not None:
        ledger = await load_publish_ledger(store)
        published_ids = set(ledger)

    for job in jobs:
        if outcome.sent >= send_limit:
            break
        job_id = extract_publish_job_id(job)
        if job_id is not None and job_id in published_ids:
            outcome.skipped_already_published += 1
            continue

        sent = False
        for attempt in range(1, transient_attempts + 1):
            try:
                await sender.send(target, job)
                sent = True
                break
            except TransientSendError as flood:
                outcome.error = str(flood)
                logger.warning(
                    "publish_flood_wait",
                    target=target,
                    retry_after=flood.retry_after,
                    attempt=attempt,
                )
                if attempt >= transient_attempts:
                    outcome.had_transient_failure = True
                    break
                await sleep(flood.retry_after + 1.0)
            except FatalTargetError as fatal:
                outcome.error = str(fatal)
                outcome.target_unusable = True
                logger.warning("publish_target_unusable", target=target, error=str(fatal))
                return outcome
            except Exception as send_err:  # noqa: BLE001 - per-job isolation
                outcome.error = str(send_err)
                if _is_fatal_target_error(send_err):
                    outcome.target_unusable = True
                    logger.warning("publish_target_unusable", target=target, error=str(send_err))
                    return outcome
                # A single malformed card must not cancel the rest of the batch.
                logger.warning("publish_job_failed", target=target, error=str(send_err))
                break

        if not sent:
            continue

        outcome.sent += 1
        outcome.delivered.append(job)
        if store is not None and job_id is not None:
            published_ids.add(job_id)
            ledger.append(job_id)
            ledger = await persist_publish_ledger(store, ledger)
        if throttle_seconds > 0:
            await sleep(throttle_seconds)

    return outcome
