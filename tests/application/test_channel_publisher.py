from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from job_ftch.application.channel_publisher import (
    DeliveryReceipt,
    FatalTargetError,
    RejectedDelivery,
    TransientSendError,
    publish_jobs,
)

pytestmark = pytest.mark.anyio


class _Job:
    def __init__(self, group_id: str, canonical_url: str | None = None) -> None:
        self.group_id = group_id
        self.canonical_url = canonical_url


class _Sender:
    """Records sends and replays a scripted failure sequence."""

    def __init__(self, failures: dict[int, Exception] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._failures = failures or {}
        self._n = 0

    async def send(self, target: str, job: Any) -> DeliveryReceipt:
        self._n += 1
        failure = self._failures.get(self._n)
        if failure is not None:
            raise failure
        self.calls.append((target, job.group_id))
        return DeliveryReceipt(target, self._n, datetime.now(UTC))


class _Store:
    def __init__(self) -> None:
        self.state: dict[str, str] = {}

    async def get_run_state(self, key: str) -> str | None:
        return self.state.get(key)

    async def set_run_state(self, key: str, value: str) -> None:
        self.state[key] = value


async def _no_sleep(_seconds: float) -> None:
    return None


async def test_publishes_up_to_send_limit() -> None:
    sender = _Sender()
    outcome = await publish_jobs(
        [_Job("a"), _Job("b"), _Job("c")],
        target="@chan",
        sender=sender,
        send_limit=2,
        sleep=_no_sleep,
    )

    assert outcome.sent == 2
    assert [job_id for _, job_id in sender.calls] == ["a", "b"]


async def test_tracking_parameters_do_not_republish_legacy_ledger_url() -> None:
    store = _Store()
    store.state["bot_publish:sent_urls"] = json.dumps(
        ["https://example.com/jobs?id=123&utm_source=old"]
    )
    sender = _Sender()
    outcome = await publish_jobs(
        [_Job("different-group", "https://example.com/jobs?id=123&UTM_medium=new")],
        target="@chan",
        sender=sender,
        store=store,
        send_limit=5,
        sleep=_no_sleep,
    )
    assert outcome.sent == 0
    assert outcome.skipped_already_published == 1
    assert sender.calls == []


async def test_tracking_cleanup_preserves_vacancy_identity_parameter() -> None:
    store = _Store()
    store.state["bot_publish:sent_urls"] = json.dumps(
        ["https://example.com/jobs?id=123&utm_source=old"]
    )
    sender = _Sender()
    outcome = await publish_jobs(
        [_Job("different-group", "https://example.com/jobs?id=456&utm_source=new")],
        target="@chan",
        sender=sender,
        store=store,
        send_limit=5,
        sleep=_no_sleep,
    )
    assert outcome.sent == 1
    assert sender.calls == [("@chan", "different-group")]


async def test_malformed_legacy_url_does_not_block_publication() -> None:
    store = _Store()
    store.state["bot_publish:sent_urls"] = json.dumps(["https://[broken"])
    sender = _Sender()
    error = None
    outcome = None
    try:
        outcome = await publish_jobs(
            [_Job("job", "https://example.com/jobs?id=456")],
            target="@chan",
            sender=sender,
            store=store,
            send_limit=5,
            sleep=_no_sleep,
        )
    except ValueError as exc:
        error = exc
    assert error is None
    assert outcome is not None
    assert outcome.sent == 1
    assert sender.calls == [("@chan", "job")]


async def test_unknown_send_outcome_is_not_sent_again_automatically() -> None:
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    class AmbiguousSender:
        def __init__(self) -> None:
            self.calls = 0

        async def send(self, target: str, job: Any) -> None:
            self.calls += 1
            raise TimeoutError("response lost after request")

    store = InMemoryStore()
    sender = AmbiguousSender()
    job = _Job("job", "https://example.com/jobs?id=456")
    first = await publish_jobs([job], target="@chan", sender=sender, store=store, send_limit=1)
    second = await publish_jobs([job], target="@chan", sender=sender, store=store, send_limit=1)

    assert first.sent == 0
    assert second.sent == 0
    assert sender.calls == 1


async def test_uncertain_first_job_does_not_hide_confirmed_later_job() -> None:
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    store = InMemoryStore()

    class FirstUnknownSender(_Sender):
        async def send(self, target: str, job: Any) -> DeliveryReceipt:
            if job.group_id == "first":
                return DeliveryReceipt(target, "unknown", datetime.now(UTC))
            return await super().send(target, job)

    sender = FirstUnknownSender()
    first, second = _Job("first"), _Job("second")
    result = await publish_jobs(
        [first, second], target="@chan", sender=sender, store=store, send_limit=2, sleep=_no_sleep
    )
    assert result.sent == 1
    assert result.delivered == [second]
    assert result.error is not None
    assert sender.calls == [("@chan", "second")]


async def test_uncertain_attempt_has_identity_for_reconciliation() -> None:
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    store = InMemoryStore()
    sender = _Sender({1: TimeoutError("response lost")})
    await publish_jobs(
        [_Job("job-123", "https://example.com/job?id=1")],
        target="@chan",
        sender=sender,
        store=store,
        send_limit=1,
        sleep=_no_sleep,
    )
    records = [r for r in store._outbox.values() if r.sink_name.startswith("telegram:")]
    assert len(records) == 2
    assert all(record.delivery_payload.get("job_id") == "job-123" for record in records)
    assert all(record.delivery_payload.get("target") == "@chan" for record in records)
    assert len({record.delivery_payload["attempt_id"] for record in records}) == 1


async def test_missing_job_identity_is_not_silently_sent() -> None:
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    sender = _Sender()
    outcome = await publish_jobs(
        [_Job("", None)],
        target="@chan",
        sender=sender,
        store=InMemoryStore(),
        send_limit=1,
        sleep=_no_sleep,
    )
    assert outcome.sent == 0
    assert outcome.error == "delivery_identity_missing"
    assert sender.calls == []


async def test_confirmed_send_marks_intent_delivered() -> None:
    from job_ftch.domain import OutboxState
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    class ConfirmedSender:
        async def send(self, target: str, job: Any) -> DeliveryReceipt:
            return DeliveryReceipt(target, 42, datetime.now(UTC))

    store = InMemoryStore()
    outcome = await publish_jobs(
        [_Job("job", "https://example.com/jobs?id=456")],
        target="@chan",
        sender=ConfirmedSender(),
        store=store,
        send_limit=1,
        sleep=_no_sleep,
    )
    assert outcome.sent == 1
    records = [r for r in store._outbox.values() if r.sink_name.startswith("telegram:")]
    assert len(records) == 2
    assert all(record.state is OutboxState.DELIVERED for record in records)
    for record in records:
        receipt = record.delivery_payload.get("receipt")
        assert isinstance(receipt, dict)
        assert receipt["message_id"] == 42


@pytest.mark.parametrize("message_id", ["unknown", 0, -1, True])
async def test_missing_message_id_remains_uncertain(message_id: int | str) -> None:
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    class MissingIdSender:
        async def send(self, target: str, job: Any) -> DeliveryReceipt:
            return DeliveryReceipt(target, message_id, datetime.now(UTC))

    store = InMemoryStore()
    result = await publish_jobs(
        [_Job("job")],
        target="@chan",
        sender=MissingIdSender(),
        store=store,
        send_limit=1,
        sleep=_no_sleep,
    )
    assert result.sent == 0
    assert result.error == "delivery_requires_reconciliation"
    assert len(await store.list_uncertain_outbox()) == 1


async def test_no_receipt_is_not_a_confirmed_channel_publication() -> None:
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    class NoReceiptSender:
        async def send(self, target: str, job: Any) -> None:
            return None

    sender = NoReceiptSender()
    store = InMemoryStore()
    result = await publish_jobs(
        [_Job("job")], target="@chan", sender=sender, store=store, send_limit=1, sleep=_no_sleep
    )
    assert result.sent == 0
    assert result.error == "delivery_requires_reconciliation"
    assert len(await store.list_uncertain_outbox()) == 1


async def test_explicit_rate_limit_can_be_retried_on_next_run() -> None:
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    sender = _Sender({1: TransientSendError(0)})
    store = InMemoryStore()
    job = _Job("job", "https://example.com/jobs?id=456")
    first = await publish_jobs(
        [job],
        target="@chan",
        sender=sender,
        store=store,
        send_limit=1,
        transient_attempts=1,
        sleep=_no_sleep,
    )
    second = await publish_jobs(
        [job],
        target="@chan",
        sender=sender,
        store=store,
        send_limit=1,
        sleep=_no_sleep,
    )
    assert first.sent == 0
    assert second.sent == 1
    assert sender.calls == [("@chan", "job")]


async def test_rejected_card_can_be_corrected_and_sent_later() -> None:
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    class RejectingSender:
        async def send(self, target: str, job: Any) -> RejectedDelivery:
            return RejectedDelivery("invalid_card")

    store = InMemoryStore()
    job = _Job("job", "https://example.com/jobs?id=456")
    first = await publish_jobs(
        [job], target="@chan", sender=RejectingSender(), store=store, send_limit=1, sleep=_no_sleep
    )
    sender = _Sender()
    second = await publish_jobs(
        [job], target="@chan", sender=sender, store=store, send_limit=1, sleep=_no_sleep
    )
    assert first.sent == 0
    assert second.sent == 1
    assert sender.calls == [("@chan", "job")]


async def test_confirmed_forbidden_target_can_recover_later() -> None:
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    store = InMemoryStore()
    job = _Job("job", "https://example.com/jobs?id=456")
    first = await publish_jobs(
        [job],
        target="@chan",
        sender=_Sender({1: FatalTargetError()}),
        store=store,
        send_limit=1,
        sleep=_no_sleep,
    )
    sender = _Sender()
    second = await publish_jobs(
        [job], target="@chan", sender=sender, store=store, send_limit=1, sleep=_no_sleep
    )
    assert first.target_unusable
    assert second.sent == 1


async def test_delivery_to_one_target_does_not_block_another() -> None:
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    store = InMemoryStore()
    job = _Job("job", "https://example.com/jobs?id=456")
    sender = _Sender()
    first = await publish_jobs(
        [job], target="@first", sender=sender, store=store, send_limit=1, sleep=_no_sleep
    )
    second = await publish_jobs(
        [job], target="@second", sender=sender, store=store, send_limit=1, sleep=_no_sleep
    )
    assert first.sent == 1
    assert second.sent == 1
    assert sender.calls == [("@first", "job"), ("@second", "job")]


async def test_legacy_ledger_protects_first_target_only() -> None:
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    store = InMemoryStore()
    await store.set_run_state("bot_publish:sent_urls", json.dumps(["https://example.com/job?id=1"]))
    job = _Job("job", "https://example.com/job?id=1")
    sender = _Sender()
    first = await publish_jobs(
        [job], target="@first", sender=sender, store=store, send_limit=1, sleep=_no_sleep
    )
    second = await publish_jobs(
        [job], target="@second", sender=sender, store=store, send_limit=1, sleep=_no_sleep
    )
    assert first.sent == 0
    assert first.skipped_already_published == 1
    assert second.sent == 1


async def test_legacy_snapshot_is_not_mutated_by_other_target() -> None:
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    store = InMemoryStore()
    job = _Job("job", "https://example.com/job?id=1")
    sender = _Sender()
    await publish_jobs(
        [_Job("old")], target="@first", sender=sender, store=store, send_limit=1, sleep=_no_sleep
    )
    await publish_jobs(
        [job], target="@second", sender=sender, store=store, send_limit=1, sleep=_no_sleep
    )
    result = await publish_jobs(
        [job], target="@first", sender=sender, store=store, send_limit=1, sleep=_no_sleep
    )
    assert result.sent == 1


async def test_legacy_snapshot_does_not_absorb_new_publications() -> None:
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    store = InMemoryStore()
    await publish_jobs(
        [_Job("new", "https://example.com/new")],
        target="@first",
        sender=_Sender(),
        store=store,
        send_limit=1,
        sleep=_no_sleep,
    )
    anchor = next(
        record
        for record in store._outbox.values()
        if record.sink_name == "publication-ledger-anchor"
    )
    assert anchor.delivery_payload["ids"] == []
    assert anchor.delivery_payload["urls"] == []


async def test_same_job_id_with_changed_url_is_not_republished() -> None:
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    store = InMemoryStore()
    sender = _Sender()
    first = await publish_jobs(
        [_Job("job", "https://example.com/old")],
        target="@first",
        sender=sender,
        store=store,
        send_limit=1,
        sleep=_no_sleep,
    )
    second = await publish_jobs(
        [_Job("job", "https://example.com/new")],
        target="@first",
        sender=sender,
        store=store,
        send_limit=1,
        sleep=_no_sleep,
    )
    assert first.sent == 1
    assert second.sent == 0
    assert sender.calls == [("@first", "job")]


async def test_sqlite_same_job_with_changed_url_is_not_republished(tmp_path) -> None:
    from job_ftch.infrastructure.stores.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "ledger.db")
    sender = _Sender()
    try:
        first = await publish_jobs(
            [_Job("job", "https://example.com/old")],
            target="@first",
            sender=sender,
            store=store,
            send_limit=1,
            sleep=_no_sleep,
        )
        second = await publish_jobs(
            [_Job("job", "https://example.com/new")],
            target="@first",
            sender=sender,
            store=store,
            send_limit=1,
            sleep=_no_sleep,
        )
        assert first.sent == 1
        assert second.sent == 0
        assert sender.calls == [("@first", "job")]
    finally:
        await store.close()


@pytest.mark.parametrize(
    "second_id,second_url",
    [
        ("job", "https://example.com/job?id=1"),
        ("job", "https://example.com/job?id=2"),
        ("regrouped", "https://example.com/job?id=1"),
    ],
)
async def test_concurrent_publishers_only_send_once(
    tmp_path, second_id: str, second_url: str
) -> None:
    import asyncio

    from job_ftch.infrastructure.stores.sqlite import SQLiteStore

    started, finish = asyncio.Event(), asyncio.Event()

    class WaitingSender:
        calls = 0

        async def send(self, target: str, job: Any) -> DeliveryReceipt:
            self.calls += 1
            if self.calls == 1:
                started.set()
                await finish.wait()
            return DeliveryReceipt(target, self.calls, datetime.now(UTC))

    first_store, second_store = (
        SQLiteStore(tmp_path / "concurrent.db"),
        SQLiteStore(tmp_path / "concurrent.db"),
    )
    sender = WaitingSender()
    job = _Job("job", "https://example.com/job?id=1")
    task = None
    try:
        task = asyncio.create_task(
            publish_jobs(
                [job],
                target="@chan",
                sender=sender,
                store=first_store,
                send_limit=1,
                sleep=_no_sleep,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=5)
        second = await asyncio.wait_for(
            publish_jobs(
                [_Job(second_id, second_url)],
                target="@chan",
                sender=sender,
                store=second_store,
                send_limit=1,
                sleep=_no_sleep,
            ),
            timeout=5,
        )
        assert sender.calls == 1
        assert second.sent == 0
        assert second.error == "delivery_requires_reconciliation"
    finally:
        finish.set()
        if task is not None:
            first = await task
            assert first.sent == 1
        await first_store.close()
        await second_store.close()


@pytest.mark.skipif(
    not __import__("os").getenv("JOB_FTCH_TEST_POSTGRES_DSN"),
    reason="isolated PostgreSQL unavailable",
)
async def test_postgres_concurrent_publishers_do_not_duplicate() -> None:
    import asyncio
    import os
    import uuid

    from job_ftch.application.tenant_store import TenantStore
    from job_ftch.infrastructure.stores.postgres import PostgreSQLStore

    started, finish = asyncio.Event(), asyncio.Event()

    class WaitingSender:
        calls = 0

        async def send(self, target: str, job: Any) -> DeliveryReceipt:
            self.calls += 1
            if self.calls == 1:
                started.set()
                await finish.wait()
            return DeliveryReceipt(target, self.calls, datetime.now(UTC))

    tenant = f"test-{uuid.uuid4().hex}"
    raw_first = PostgreSQLStore(os.environ["JOB_FTCH_TEST_POSTGRES_DSN"])
    raw_second = PostgreSQLStore(os.environ["JOB_FTCH_TEST_POSTGRES_DSN"])
    first_store, second_store = TenantStore(tenant, raw_first), TenantStore(tenant, raw_second)
    sender = WaitingSender()
    task = None
    try:
        task = asyncio.create_task(
            publish_jobs(
                [_Job("job", "https://example.com/old")],
                target="@chan",
                sender=sender,
                store=first_store,
                send_limit=1,
                sleep=_no_sleep,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=15)
        second = await asyncio.wait_for(
            publish_jobs(
                [_Job("job", "https://example.com/new")],
                target="@chan",
                sender=sender,
                store=second_store,
                send_limit=1,
                sleep=_no_sleep,
            ),
            timeout=15,
        )
        assert sender.calls == 1
        assert second.sent == 0
        assert second.error == "delivery_requires_reconciliation"
    finally:
        finish.set()
        if task is not None:
            assert (await task).sent == 1
        await raw_first.close()
        await raw_second.close()


async def test_acknowledgement_write_failure_does_not_resend() -> None:
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    class FailedAcknowledgementStore(InMemoryStore):
        fail = True

        async def set_run_state(self, key: str, value: str, **kwargs: Any) -> None:
            if key == "bot_publish:delivery_receipts" and self.fail:
                self.fail = False
                raise OSError("acknowledgement store unavailable")
            await super().set_run_state(key, value, **kwargs)

    class ConfirmedSender:
        calls = 0

        async def send(self, target: str, job: Any) -> DeliveryReceipt:
            self.calls += 1
            return DeliveryReceipt(target, 42, datetime.now(UTC))

    store = FailedAcknowledgementStore()
    sender = ConfirmedSender()
    job = _Job("job", "https://example.com/job?id=1")
    with pytest.raises(OSError):
        await publish_jobs(
            [job], target="@chan", sender=sender, store=store, send_limit=1, sleep=_no_sleep
        )
    second = await publish_jobs(
        [job], target="@chan", sender=sender, store=store, send_limit=1, sleep=_no_sleep
    )
    assert sender.calls == 1
    assert second.sent == 0
    assert second.error == "delivery_requires_reconciliation"


async def test_changed_attempt_is_not_reported_as_confirmed_delivery() -> None:
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    class ConflictingStore(InMemoryStore):
        async def confirm_uncertain_outbox(self, record, receipt):
            return None

    store = ConflictingStore()
    sender = _Sender()
    result = await publish_jobs(
        [_Job("job")], target="@chan", sender=sender, store=store, send_limit=1, sleep=_no_sleep
    )
    assert result.sent == 0
    assert result.error == "delivery_requires_reconciliation"
    assert result.receipts == []
    assert await store.get_run_state("bot_publish:sent_ids") is None


async def test_missing_confirmation_capability_does_not_claim_success() -> None:
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    class MissingConfirmationStore(InMemoryStore):
        confirm_uncertain_outbox = None

    store = MissingConfirmationStore()
    error = None
    result = None
    try:
        result = await publish_jobs(
            [_Job("job")],
            target="@chan",
            sender=_Sender(),
            store=store,
            send_limit=1,
            sleep=_no_sleep,
        )
    except Exception as exc:
        error = exc
    assert error is None
    assert result is not None
    assert result.sent == 0
    assert result.error == "delivery_requires_reconciliation"


async def test_delivered_intent_survives_missing_legacy_ledger() -> None:
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    store = InMemoryStore()
    job = _Job("job", "https://example.com/jobs?id=456")
    sender = _Sender()
    await publish_jobs(
        [job], target="@chan", sender=sender, store=store, send_limit=1, sleep=_no_sleep
    )
    await store.set_run_state("bot_publish:sent_ids", "[]")
    await store.set_run_state("bot_publish:sent_urls", "[]")
    second = await publish_jobs(
        [job], target="@chan", sender=sender, store=store, send_limit=1, sleep=_no_sleep
    )
    assert second.skipped_already_published == 1
    assert second.error is None
    assert sender.calls == [("@chan", "job")]


async def test_ledger_skips_already_published() -> None:
    store = _Store()
    first = _Sender()
    await publish_jobs(
        [_Job("a")], target="@chan", sender=first, store=store, send_limit=5, sleep=_no_sleep
    )

    second = _Sender()
    outcome = await publish_jobs(
        [_Job("a"), _Job("b")],
        target="@chan",
        sender=second,
        store=store,
        send_limit=5,
        sleep=_no_sleep,
    )

    assert outcome.sent == 1
    assert outcome.skipped_already_published == 1
    assert [job_id for _, job_id in second.calls] == ["b"]


async def test_url_ledger_skips_regrouped_vacancy() -> None:
    store = _Store()
    first = _Sender()
    await publish_jobs(
        [_Job("group-a", "HTTPS://Example.com/jobs/1#card")],
        target="@chan",
        sender=first,
        store=store,
        send_limit=5,
        sleep=_no_sleep,
    )

    second = _Sender()
    outcome = await publish_jobs(
        [
            _Job("group-b", "https://example.com/jobs/1"),
            _Job("group-c", "https://example.com/jobs/2"),
        ],
        target="@chan",
        sender=second,
        store=store,
        send_limit=5,
        sleep=_no_sleep,
    )

    assert outcome.sent == 1
    assert outcome.skipped_already_published == 1
    assert [job_id for _, job_id in second.calls] == ["group-c"]
    assert json.loads(store.state["bot_publish:sent_urls"]) == [
        "https://example.com/jobs/1",
        "https://example.com/jobs/2",
    ]


async def test_url_ledger_updates_only_after_successful_send() -> None:
    store = _Store()
    sender = _Sender(failures={1: ValueError("can't parse entities")})
    outcome = await publish_jobs(
        [
            _Job("group-a", "https://example.com/jobs/bad"),
            _Job("group-b", "https://example.com/jobs/good"),
        ],
        target="@chan",
        sender=sender,
        store=store,
        send_limit=5,
        sleep=_no_sleep,
    )

    assert outcome.sent == 1
    assert json.loads(store.state["bot_publish:sent_urls"]) == ["https://example.com/jobs/good"]


async def test_flood_wait_is_retried_then_gives_up() -> None:
    """Chat delivery never retried flood waits; the merged path always does."""
    sender = _Sender(failures={1: TransientSendError(1.0), 2: TransientSendError(1.0)})
    outcome = await publish_jobs(
        [_Job("a"), _Job("b")], target="@chan", sender=sender, send_limit=5, sleep=_no_sleep
    )

    assert outcome.had_transient_failure is True
    # The flooded job is abandoned, the batch continues.
    assert [job_id for _, job_id in sender.calls] == ["b"]
    assert outcome.sent == 1


async def test_flood_wait_succeeds_on_retry() -> None:
    sender = _Sender(failures={1: TransientSendError(0.5)})
    outcome = await publish_jobs(
        [_Job("a")], target="@chan", sender=sender, send_limit=5, sleep=_no_sleep
    )

    assert outcome.sent == 1
    assert outcome.had_transient_failure is False


async def test_bad_card_skips_job_without_cancelling_batch() -> None:
    """Chat delivery used to break on the first error, dropping the remainder."""
    sender = _Sender(failures={1: ValueError("can't parse entities")})
    outcome = await publish_jobs(
        [_Job("a"), _Job("b")], target="@chan", sender=sender, send_limit=5, sleep=_no_sleep
    )

    assert outcome.sent == 1
    assert [job_id for _, job_id in sender.calls] == ["b"]
    assert outcome.had_transient_failure is False
    assert outcome.target_unusable is False


async def test_connection_error_keeps_delivery_retryable() -> None:
    sender = _Sender(failures={1: ConnectionError("ClientConnectorError: connect call failed")})
    outcome = await publish_jobs(
        [_Job("a"), _Job("b")], target="@chan", sender=sender, send_limit=5, sleep=_no_sleep
    )

    assert outcome.sent == 1
    assert outcome.had_transient_failure is True
    assert [job_id for _, job_id in sender.calls] == ["b"]


async def test_fatal_target_stops_immediately() -> None:
    sender = _Sender(failures={1: FatalTargetError("bot is not a member of the channel")})
    outcome = await publish_jobs(
        [_Job("a"), _Job("b")], target="@chan", sender=sender, send_limit=5, sleep=_no_sleep
    )

    assert outcome.sent == 0
    assert outcome.target_unusable is True
    assert sender.calls == []


async def test_generic_error_naming_a_dead_target_is_treated_as_fatal() -> None:
    sender = _Sender(failures={1: RuntimeError("Forbidden: bot was kicked from the chat")})
    outcome = await publish_jobs(
        [_Job("a"), _Job("b")], target="@chan", sender=sender, send_limit=5, sleep=_no_sleep
    )

    assert outcome.target_unusable is True
    assert outcome.sent == 0


async def test_card_rejection_does_not_count_as_delivery_or_ledger_entry() -> None:
    class _RejectingSender:
        async def send(self, _target: str, _job: Any) -> RejectedDelivery:
            return RejectedDelivery("card_validation_rejected")

    store = _Store()
    outcome = await publish_jobs(
        [_Job("rejected", "https://example.com/rejected")],
        target="@chan",
        sender=_RejectingSender(),
        store=store,
        send_limit=5,
        sleep=_no_sleep,
    )

    assert outcome.sent == 0
    assert outcome.card_validation_rejections == 1
    assert "bot_publish:sent_ids" not in store.state
    assert "bot_publish:sent_urls" not in store.state


async def test_terminal_card_rejection_does_not_keep_batch_retryable() -> None:
    class _RejectThenSend:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def send(self, _target: str, job: Any) -> object:
            self.calls.append(job.group_id)
            if job.group_id == "rejected":
                return RejectedDelivery("card_validation_rejected")
            return None

    sender = _RejectThenSend()
    outcome = await publish_jobs(
        [_Job("rejected"), _Job("valid")],
        target="@chan",
        sender=sender,
        send_limit=5,
        sleep=_no_sleep,
    )

    assert sender.calls == ["rejected", "valid"]
    assert outcome.sent == 1
    assert outcome.terminal_skips == 1
    assert outcome.terminal_skip_reason == "terminal_rejections:card_validation_rejected:1"
    assert outcome.error is None


async def test_delivery_receipt_is_persisted_before_publish_ledger() -> None:
    class _ReceiptSender:
        async def send(self, _target: str, job: Any) -> DeliveryReceipt:
            del job
            return DeliveryReceipt("-100", 42, datetime.now(UTC))

    store = _Store()
    outcome = await publish_jobs(
        [_Job("delivered", "https://example.com/delivered")],
        target="@chan",
        sender=_ReceiptSender(),
        store=store,
        send_limit=5,
        sleep=_no_sleep,
    )

    assert outcome.sent == 1
    assert json.loads(store.state["bot_publish:delivery_receipts"])[0]["message_id"] == 42
