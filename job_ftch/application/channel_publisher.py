"""Single implementation of "deliver accepted vacancies to a target".

Both the manual `/run` path and the background scheduler used to carry their
own copy of this loop. The copies drifted: only the scheduler retried flood
waits and kept a retry window, only the chat path stopped the batch on a send
error. Delivery semantics now live here, and adapters supply nothing but a
transport.

Publication tracking uses the tenant ledger and, when available, durable outbox:

- `bot_publish:sent_ids` keeps group_id/job_id for the legacy path and feedback;
- `bot_publish:sent_urls` keeps normalized canonical_url values so regrouping a
  vacancy does not publish it again under a fresh group_id.
- Uncertain sends are not automatically repeated until an operator reconciles them.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from hashlib import sha256
from typing import TYPE_CHECKING, Protocol, cast
from uuid import uuid4

import structlog

from job_ftch.application.publish_ledger import (
    _load_string_list,
    _persist_string_list,
    extract_publish_canonical_url,
    extract_publish_job_id,
    load_publish_ledger,
    load_publish_url_ledger,
    persist_publish_ledger,
    persist_publish_url_ledger,
)
from job_ftch.domain import OutboxRecord, OutboxState

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from datetime import datetime

    from job_ftch.application.capabilities import OutboxCapable
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

    async def send(self, target: str, job: Job) -> object: ...


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    chat_id: str
    message_id: int | str
    confirmed_at: datetime


@dataclass(frozen=True, slots=True)
class RejectedDelivery:
    """A terminal rejection of one card, not a retryable transport failure."""

    reason: str


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
    receipts: list[DeliveryReceipt] = field(default_factory=list)
    card_validation_rejections: int = 0
    terminal_skips: int = 0
    terminal_skip_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def terminal_skip_reason(self) -> str:
        if not self.terminal_skip_reasons:
            return ""
        details = ",".join(
            f"{reason}:{count}" for reason, count in sorted(self.terminal_skip_reasons.items())
        )
        return f"terminal_rejections:{details}"


def remaining_publish_candidates(total_candidates: int, outcome: PublishOutcome) -> int:
    """Count only candidates that still need a retry.

    Terminal card rejections are handled outcomes. They must never keep the
    durable publish window open after the rest of the batch was processed.
    ``getattr`` keeps lightweight test doubles and older callers compatible.
    """
    return max(
        0,
        total_candidates
        - outcome.sent
        - outcome.skipped_already_published
        - int(getattr(outcome, "terminal_skips", 0) or 0),
    )


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

    Confirmed sends are recorded in the ledger. Stores supporting the durable
    outbox also retain uncertain sends for operator reconciliation.
    """
    outcome = PublishOutcome()
    if send_limit <= 0 or not jobs:
        return outcome

    id_ledger: list[str] = []
    url_ledger: list[str] = []
    published_ids: set[str] = set()
    published_urls: set[str] = set()
    target_ids: list[str] = []
    target_key: str | None = None
    if store is not None:
        id_ledger = await load_publish_ledger(store)
        url_ledger = await load_publish_url_ledger(store)
        enqueue = getattr(store, "enqueue_outbox", None)
        if callable(enqueue):
            target_key = sha256(target.encode()).hexdigest()
            anchor_key = sha256(b"publication:legacy-ledger-owner").hexdigest()
            anchor = await enqueue(
                OutboxRecord(
                    outbox_id=anchor_key,
                    observation_id=anchor_key,
                    content_hash=anchor_key,
                    decision_version="publication-v1",
                    sink_name="publication-ledger-anchor",
                    idempotency_key=anchor_key,
                    delivery_payload={
                        "target": target_key,
                        "ids": list(id_ledger),
                        "urls": list(url_ledger),
                    },
                    state=OutboxState.UNCERTAIN,
                )
            )
            await cast("OutboxCapable", store).mark_outbox_delivered(anchor.idempotency_key)
            target_ids = await _load_string_list(store, f"bot_publish:target:{target_key}:ids")
            published_ids.update(target_ids)
            if anchor.delivery_payload.get("target") == target_key:
                published_ids.update(anchor.delivery_payload.get("ids", []))
                published_urls = set(anchor.delivery_payload.get("urls", []))
        else:
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

        intents: list[OutboxRecord] = []
        enqueue = getattr(store, "enqueue_outbox", None)
        if callable(enqueue):
            identities = [
                (kind, value) for kind, value in (("id", job_id), ("url", publish_url)) if value
            ]
            if not identities:
                outcome.error = "delivery_identity_missing"
                continue
            attempt_id = uuid4().hex
            blocked = False
            for kind, identity in identities:
                content_hash = sha256(f"{kind}:{identity}".encode()).hexdigest()
                key = sha256(f"telegram:{target}:{content_hash}".encode()).hexdigest()
                intent = await enqueue(
                    OutboxRecord(
                        outbox_id=key,
                        observation_id=content_hash,
                        content_hash=content_hash,
                        decision_version="publication-v1",
                        sink_name=f"telegram:{sha256(target.encode()).hexdigest()}",
                        idempotency_key=key,
                        delivery_payload={
                            "attempt_id": attempt_id,
                            "job_id": job_id,
                            "target": target,
                        },
                        state=OutboxState.UNCERTAIN,
                    )
                )
                if intent.delivery_payload.get("attempt_id") != attempt_id:
                    release = getattr(store, "release_uncertain_outbox", None)
                    if callable(release):
                        for acquired in intents:
                            await release(acquired)
                    if intent.state is OutboxState.DELIVERED:
                        outcome.skipped_already_published += 1
                    else:
                        outcome.error = "delivery_requires_reconciliation"
                        outcome.had_transient_failure = True
                    blocked = True
                    break
                intents.append(intent)
            if blocked:
                continue

        sent = False
        for attempt in range(1, transient_attempts + 1):
            try:
                delivery = await sender.send(target, job)
                if isinstance(delivery, RejectedDelivery):
                    release = getattr(store, "release_uncertain_outbox", None)
                    if callable(release):
                        for intent in intents:
                            await release(intent)
                    outcome.card_validation_rejections += 1
                    outcome.terminal_skips += 1
                    outcome.terminal_skip_reasons[delivery.reason] = (
                        outcome.terminal_skip_reasons.get(delivery.reason, 0) + 1
                    )
                    logger.info(
                        "publish_card_rejected",
                        target=target,
                        reason=delivery.reason,
                    )
                    break
                if isinstance(delivery, DeliveryReceipt) and (
                    not str(delivery.message_id).isdigit() or int(delivery.message_id) <= 0
                ):
                    outcome.error = "delivery_requires_reconciliation"
                    outcome.had_transient_failure = True
                    break
                if intents and not isinstance(delivery, DeliveryReceipt):
                    outcome.error = "delivery_requires_reconciliation"
                    outcome.had_transient_failure = True
                    break
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
                    release = getattr(store, "release_uncertain_outbox", None)
                    if callable(release):
                        for intent in intents:
                            await release(intent)
                    outcome.had_transient_failure = True
                    break
                await sleep(flood.retry_after + 1.0)
            except FatalTargetError as fatal:
                release = getattr(store, "release_uncertain_outbox", None)
                if callable(release):
                    for intent in intents:
                        await release(intent)
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

        if store is not None and isinstance(delivery, DeliveryReceipt):
            from job_ftch.application.publish_ledger import persist_delivery_receipt

            await persist_delivery_receipt(store, delivery)

        if intents:
            confirm = getattr(store, "confirm_uncertain_outbox", None)
            if not callable(confirm) or not isinstance(delivery, DeliveryReceipt):
                outcome.error = "delivery_requires_reconciliation"
                continue
            confirmation_failed = False
            for intent in intents:
                confirmed = await confirm(
                    intent,
                    {
                        "chat_id": delivery.chat_id,
                        "message_id": delivery.message_id,
                        "confirmed_at": delivery.confirmed_at.isoformat(),
                    },
                )
                if confirmed is None:
                    outcome.error = "delivery_requires_reconciliation"
                    confirmation_failed = True
                    break
            if confirmation_failed:
                continue

        outcome.sent += 1
        outcome.delivered.append(job)
        if isinstance(delivery, DeliveryReceipt):
            outcome.receipts.append(delivery)
        if store is not None:
            if job_id is not None:
                published_ids.add(job_id)
                id_ledger.append(job_id)
                id_ledger = await persist_publish_ledger(store, id_ledger)
                if target_key is not None:
                    target_ids.append(job_id)
                    target_ids = await _persist_string_list(
                        store, f"bot_publish:target:{target_key}:ids", target_ids
                    )
            if publish_url is not None:
                published_urls.add(publish_url)
                url_ledger.append(publish_url)
                url_ledger = await persist_publish_url_ledger(store, url_ledger)
        if throttle_seconds > 0:
            await sleep(throttle_seconds)

    return outcome
