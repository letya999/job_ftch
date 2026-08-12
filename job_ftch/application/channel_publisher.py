"""Single implementation of "deliver accepted vacancies to a target".

Both the manual `/run` path and the background scheduler used to carry their
own copy of this loop. The copies drifted: only the scheduler retried flood
waits and kept a retry window, only the chat path stopped the batch on a send
error. Delivery semantics now live here, and adapters supply nothing but a
transport.

Publish idempotency is durable in the tenant run-state ledger:

- `bot_publish:sent_ids` keeps group_id/job_id for the legacy path and feedback;
- `bot_publish:sent_urls` keeps normalized canonical_url values so regrouping a
  vacancy does not publish it again under a fresh group_id.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import structlog

from job_ftch.application.publish_ledger import (
    extract_publish_canonical_url,
    extract_publish_job_id,
    load_publish_ledger,
    load_publish_url_ledger,
    persist_publish_ledger,
    persist_publish_url_ledger,
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
_RETRYABLE_SEND_MARKERS = (
    "connection",
    "connect call failed",
    "timed out",
    "timeout",
    "temporarily unavailable",
    "server disconnected",
    "502",
    "503",
    "504",
)
_RETRYABLE_SEND_TYPES = {
    "ClientConnectorError",
    "ClientConnectionError",
    "ClientOSError",
    "ConnectionError",
    "TimeoutError",
}


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
    # True for rate-limit exhaustion or transport outages, so callers can keep
    # a retry window open. The publish ledger makes retries idempotent.
    had_transient_failure: bool = False
    target_unusable: bool = False
    delivered: list[Job] = field(default_factory=list)


def _is_fatal_target_error(error: BaseException) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in _FATAL_TARGET_MARKERS)


def _is_retryable_send_error(error: BaseException) -> bool:
    """Recognize transport outages that must keep the delivery window open."""
    if any(cls.__name__ in _RETRYABLE_SEND_TYPES for cls in type(error).__mro__):
        return True
    text = str(error).lower()
    return any(marker in text for marker in _RETRYABLE_SEND_MARKERS)


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
    Both the legacy id ledger and canonical URL ledger are checked.
    """
    outcome = PublishOutcome()
    if send_limit <= 0 or not jobs:
        return outcome

    id_ledger: list[str] = []
    url_ledger: list[str] = []
    published_ids: set[str] = set()
    published_urls: set[str] = set()
    if store is not None:
        id_ledger = await load_publish_ledger(store)
        url_ledger = await load_publish_url_ledger(store)
        published_ids = set(id_ledger)
        published_urls = set(url_ledger)

    for job in jobs:
        if outcome.sent >= send_limit:
            break
        job_id = extract_publish_job_id(job)
        publish_url = extract_publish_canonical_url(job)
        already_by_id = job_id is not None and job_id in published_ids
        already_by_url = publish_url is not None and publish_url in published_urls
        if already_by_id or already_by_url:
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
                if _is_retryable_send_error(send_err):
                    outcome.had_transient_failure = True
                # A single malformed card must not cancel the rest of the batch.
                logger.warning("publish_job_failed", target=target, error=str(send_err))
                break

        if not sent:
            continue

        outcome.sent += 1
        outcome.delivered.append(job)
        if store is not None:
            if job_id is not None:
                published_ids.add(job_id)
                id_ledger.append(job_id)
                id_ledger = await persist_publish_ledger(store, id_ledger)
            if publish_url is not None:
                published_urls.add(publish_url)
                url_ledger.append(publish_url)
                url_ledger = await persist_publish_url_ledger(store, url_ledger)
        if throttle_seconds > 0:
            await sleep(throttle_seconds)

    return outcome
