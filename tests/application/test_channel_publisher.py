from __future__ import annotations

import json
from typing import Any

import pytest

from job_ftch.application.channel_publisher import (
    FatalTargetError,
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

    async def send(self, target: str, job: Any) -> None:
        self._n += 1
        failure = self._failures.get(self._n)
        if failure is not None:
            raise failure
        self.calls.append((target, job.group_id))


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
    assert json.loads(store.state["bot_publish:sent_urls"]) == [
        "https://example.com/jobs/good"
    ]


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
