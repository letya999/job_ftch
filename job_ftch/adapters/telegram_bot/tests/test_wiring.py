"""Wiring smoke tests for the aiogram-based Telegram bot adapter.

These verify that the single living bot implementation (World A: aiogram
Dispatcher + routers + middlewares) assembles correctly and embeds the
job_ftch library exclusively through ``TenantRunner``. They are skipped when
the optional ``telegram`` extra (aiogram) is not installed.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path  # noqa: TC003
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("aiogram")

from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendMessage

from job_ftch.adapters.telegram_bot.config import TelegramBotConfig
from job_ftch.adapters.telegram_bot.handlers import (
    base,
    channel,
    examples,
    pipeline,
    schedule,
    sources,
)
from job_ftch.adapters.telegram_bot.main import (
    BOT_DESCRIPTION,
    BOT_SHORT_DESCRIPTION,
    _run_scheduler_loop,
    build_bot,
    build_dispatcher,
    configure_bot,
)
from job_ftch.domain.models import MatchDecision, PostType


@pytest.fixture(autouse=True)
def _reset_routers() -> None:
    """Reset parent_router for all global routers to allow re-attachment in tests."""
    for router in [
        base.router,
        channel.router,
        examples.router,
        pipeline.router,
        schedule.router,
        sources.router,
    ]:
        router._parent_router = None


def _config() -> TelegramBotConfig:
    return TelegramBotConfig(
        token="123456:test-token",
        allowed_user_ids=(1,),
        admin_user_ids=(1,),
        rate_limit_seconds=0.0,
    )


def test_build_bot_uses_configured_token() -> None:
    bot = build_bot(_config())
    assert bot.token == "123456:test-token"


@pytest.mark.asyncio
async def test_configure_bot_sets_commands_and_public_descriptions() -> None:
    bot = MagicMock()
    bot.set_my_description = AsyncMock()
    bot.set_my_short_description = AsyncMock()
    bot.set_my_commands = AsyncMock()

    await configure_bot(bot, _config())

    bot.set_my_description.assert_awaited_once_with(BOT_DESCRIPTION)
    bot.set_my_short_description.assert_awaited_once_with(BOT_SHORT_DESCRIPTION)
    bot.set_my_commands.assert_awaited_once()


def test_build_dispatcher_registers_all_routers() -> None:
    runner = MagicMock()
    dispatcher = build_dispatcher(runner=runner, config=_config())

    router_names = {router.name for router in dispatcher.sub_routers}
    assert {"base", "pipeline", "sources"} <= router_names


@pytest.mark.asyncio
async def test_di_middleware_injects_only_what_handlers_read() -> None:
    """`embedding_provider`/`reranker` were injected as None and never read;
    handlers take providers from the tenant runtime instead."""
    from job_ftch.adapters.telegram_bot.middlewares.di import DIMiddleware

    runner = MagicMock()
    config = _config()
    seen: dict[str, object] = {}

    async def handler(_event: object, data: dict[str, object]) -> str:
        seen.update(data)
        return "ok"

    result = await DIMiddleware(runner=runner, config=config)(handler, MagicMock(), {})

    assert result == "ok"
    assert seen == {"runner": runner, "config": config}


def test_build_dispatcher_assembles_without_optional_providers() -> None:
    dispatcher = build_dispatcher(runner=MagicMock(), config=_config())

    assert dispatcher.sub_routers


@pytest.mark.asyncio
async def test_scheduler_loop_uses_publish_owner_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"sleep": 0}

    async def fake_sleep(seconds: float) -> None:
        # Publish throttling also routes through this patch point, so break on
        # the cycle boundary specifically rather than on the first sleep.
        calls["sleep"] += 1
        if seconds >= 60:
            raise asyncio.CancelledError

    monkeypatch.setattr("job_ftch.adapters.telegram_bot.main.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(
        "job_ftch.adapters.telegram_bot.sender.format_vacancy_card",
        lambda _job: "card",
    )

    store_state: dict[str, str] = {}

    async def _get_state(key: str) -> str | None:
        return store_state.get(key)

    async def _set_state(key: str, value: str) -> None:
        store_state[key] = value

    store = MagicMock()
    store.get_run_state = MagicMock(side_effect=_get_state)
    store.set_run_state = MagicMock(side_effect=_set_state)

    runner = MagicMock()
    runner.get_runtime.return_value = SimpleNamespace(
        settings=SimpleNamespace(
            bot_send_limit_per_run=15,
        ),
        store=store,
    )
    runner.default_tenant_id.return_value = "tenant"
    runner.tenant_ids.return_value = ["tenant"]
    runner.get_schedule_interval = MagicMock(return_value=asyncio.Future())
    runner.get_schedule_interval.return_value.set_result(1)
    runner.get_publish_channel = MagicMock(return_value=asyncio.Future())
    runner.get_publish_channel.return_value.set_result("@out")
    runner.get_publish_user_id = MagicMock(return_value=asyncio.Future())
    runner.get_publish_user_id.return_value.set_result("123")
    runner.has_candidate_profile_data = MagicMock(return_value=asyncio.Future())
    runner.has_candidate_profile_data.return_value.set_result(True)
    runner.run_tenant = MagicMock(return_value=asyncio.Future())
    runner.run_tenant.return_value.set_result(SimpleNamespace(emitted=1))
    runner.latest_jobs = MagicMock(return_value=asyncio.Future())
    runner.latest_jobs.return_value.set_result(
        [
            SimpleNamespace(
                post_type=PostType.JOB_POSTING,
                routing_decision=MatchDecision.ACCEPT,
                quality_score=1.0,
                best_score=0.9,
                fetched_at=None,
            )
        ]
    )
    bot = MagicMock()
    bot.send_message = MagicMock(return_value=asyncio.Future())
    bot.send_message.return_value.set_result(None)

    with pytest.raises(asyncio.CancelledError):
        await _run_scheduler_loop(runner, bot)

    runner.run_tenant.assert_called_once_with("tenant", user_id="123")
    runner.has_candidate_profile_data.assert_called_once_with("tenant", "123")
    runner.latest_jobs.assert_called_once_with(
        "tenant",
        limit=300,
        since=runner.latest_jobs.call_args.kwargs["since"],
        user_id="123",
    )
    assert bot.send_message.call_count == 2
    sent_targets = [call.args[0] for call in bot.send_message.call_args_list]
    assert "@out" in sent_targets
    assert "123" in sent_targets
    assert store_state["bot_scheduler:last_run_emitted"] == "1"
    assert store_state["bot_scheduler:last_publish_sent"] == "1"
    assert store_state["bot_scheduler:last_publish_error"] == ""


@pytest.mark.asyncio
async def test_scheduler_retries_run_left_incomplete_by_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_sleep(_seconds: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr("job_ftch.adapters.telegram_bot.main.asyncio.sleep", fake_sleep)

    now = datetime.now(UTC)
    store_state: dict[str, str] = {
        "bot_scheduler:last_attempt_at": (now - timedelta(seconds=30)).isoformat(),
        "bot_scheduler:last_success_at": (now - timedelta(seconds=60)).isoformat(),
    }

    async def _get_state(key: str) -> str | None:
        return store_state.get(key)

    async def _set_state(key: str, value: str) -> None:
        store_state[key] = value

    store = MagicMock()
    store.get_run_state = MagicMock(side_effect=_get_state)
    store.set_run_state = MagicMock(side_effect=_set_state)
    runner = MagicMock()
    runner.get_runtime.return_value = SimpleNamespace(
        settings=SimpleNamespace(bot_send_limit_per_run=15), store=store
    )
    runner.tenant_ids.return_value = ["tenant"]
    runner.get_schedule_interval = AsyncMock(return_value=3600)
    runner.get_publish_channel = AsyncMock(return_value="@out")
    runner.get_publish_user_id = AsyncMock(return_value="123")
    runner.has_candidate_profile_data = AsyncMock(return_value=True)
    runner.run_tenant = AsyncMock(return_value=SimpleNamespace(emitted=0))
    bot = MagicMock()
    bot.send_message = AsyncMock()

    with pytest.raises(asyncio.CancelledError):
        await _run_scheduler_loop(runner, bot)

    runner.run_tenant.assert_awaited_once_with("tenant", user_id="123")


@pytest.mark.asyncio
async def test_scheduler_recovers_pending_publish_before_next_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_sleep(_seconds: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr("job_ftch.adapters.telegram_bot.main.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(
        "job_ftch.adapters.telegram_bot.sender.format_vacancy_card", lambda _job: "card"
    )

    async def _send_card(_self: object, _target: str, _job: object) -> None:
        return None

    monkeypatch.setattr(
        "job_ftch.adapters.telegram_bot.main.TelegramCardSender.send", _send_card
    )

    async def _publish(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            sent=1,
            skipped_already_published=0,
            error=None,
            had_transient_failure=False,
        )

    monkeypatch.setattr("job_ftch.adapters.telegram_bot.main.publish_jobs", _publish)

    store_state: dict[str, str] = {
        "bot_scheduler:last_attempt_at": "2099-01-01T00:00:00+00:00",
        "bot_scheduler:last_success_at": "2099-01-01T00:01:00+00:00",
        "bot_scheduler:pending_publish_since": "2026-08-02T16:00:00+00:00",
        "bot_scheduler:last_publish_attempt_at": "2026-08-02T16:01:00+00:00",
    }

    async def _get_state(key: str) -> str | None:
        return store_state.get(key)

    async def _set_state(key: str, value: str) -> None:
        store_state[key] = value

    store = MagicMock()
    store.get_run_state = MagicMock(side_effect=_get_state)
    store.set_run_state = MagicMock(side_effect=_set_state)
    runner = MagicMock()
    runner.get_runtime.return_value = SimpleNamespace(
        settings=SimpleNamespace(bot_send_limit_per_run=15), store=store
    )
    runner.tenant_ids.return_value = ["tenant"]
    runner.get_schedule_interval = AsyncMock(return_value=3600)
    runner.get_publish_channel = AsyncMock(return_value="@out")
    runner.get_publish_user_id = AsyncMock(return_value="123")
    runner.has_candidate_profile_data = AsyncMock(return_value=True)
    runner.run_tenant = AsyncMock()
    runner.latest_jobs = AsyncMock(
        return_value=[
            SimpleNamespace(
                group_id="group-1",
                post_type=PostType.JOB_POSTING,
                routing_decision=MatchDecision.ACCEPT,
                quality_score=1.0,
                best_score=0.9,
            )
        ]
    )
    bot = MagicMock()
    bot.send_message = AsyncMock()

    with pytest.raises(asyncio.CancelledError):
        await _run_scheduler_loop(runner, bot)

    runner.run_tenant.assert_not_called()
    runner.latest_jobs.assert_awaited_once()
    assert store_state["bot_scheduler:pending_publish_since"] == ""
    assert store_state["bot_scheduler:last_publish_sent"] == "1"


@pytest.mark.asyncio
async def test_scheduler_loop_does_not_warn_when_jobs_are_grouped_for_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.adapters.telegram_bot import main as bot_main

    calls = {"sleep": 0}

    async def fake_sleep(_seconds: float) -> None:
        calls["sleep"] += 1
        if calls["sleep"] > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr("job_ftch.adapters.telegram_bot.main.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(
        "job_ftch.adapters.telegram_bot.sender.format_vacancy_card",
        lambda job: f"card:{job.group_id}",
    )
    warning = MagicMock()
    monkeypatch.setattr(bot_main.logger, "warning", warning)

    store_state: dict[str, str] = {}

    async def _get_state(key: str) -> str | None:
        return store_state.get(key)

    async def _set_state(key: str, value: str) -> None:
        store_state[key] = value

    store = MagicMock()
    store.get_run_state = AsyncMock(side_effect=_get_state)
    store.set_run_state = AsyncMock(side_effect=_set_state)

    runner = MagicMock()
    runner.get_runtime.return_value = SimpleNamespace(
        settings=SimpleNamespace(bot_send_limit_per_run=15),
        store=store,
    )
    runner.tenant_ids.return_value = ["tenant"]
    runner.get_schedule_interval = AsyncMock(return_value=1)
    runner.get_publish_channel = AsyncMock(return_value="@out")
    runner.get_publish_user_id = AsyncMock(return_value="123")
    runner.has_candidate_profile_data = AsyncMock(return_value=True)
    runner.run_tenant = AsyncMock(return_value=SimpleNamespace(emitted=2))
    runner.latest_jobs = AsyncMock(
        return_value=[
            SimpleNamespace(
                group_id="group-merged",
                post_type=PostType.JOB_POSTING,
                routing_decision=MatchDecision.ACCEPT,
                quality_score=1.0,
                best_score=0.9,
            )
        ]
    )

    bot = MagicMock()
    bot.send_message = AsyncMock()

    with pytest.raises(asyncio.CancelledError):
        await _run_scheduler_loop(runner, bot)

    warning_events = [call.args[0] for call in warning.call_args_list if call.args]
    assert "bot_delivery_partial_loss" not in warning_events
    assert store_state["bot_scheduler:last_publish_sent"] == "1"
    sent_targets = [call.args[0] for call in bot.send_message.call_args_list]
    assert sent_targets == ["@out", "123"]


@pytest.mark.asyncio
async def test_scheduler_loop_skips_publish_channel_without_owner_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"sleep": 0}

    async def fake_sleep(_seconds: float) -> None:
        calls["sleep"] += 1
        if calls["sleep"] > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr("job_ftch.adapters.telegram_bot.main.asyncio.sleep", fake_sleep)

    store = MagicMock()
    store.get_run_state = AsyncMock(return_value=None)
    store.set_run_state = AsyncMock()

    runner = MagicMock()
    runner.get_runtime.return_value = SimpleNamespace(
        settings=SimpleNamespace(bot_send_limit_per_run=15),
        store=store,
    )
    runner.tenant_ids.return_value = ["tenant"]
    runner.get_schedule_interval = AsyncMock(return_value=1)
    runner.get_publish_channel = AsyncMock(return_value="@out")
    runner.get_publish_user_id = AsyncMock(return_value="123")
    runner.has_candidate_profile_data = AsyncMock(return_value=False)
    runner.run_tenant = AsyncMock()
    runner.latest_jobs = AsyncMock(return_value=[])
    bot = MagicMock()

    with pytest.raises(asyncio.CancelledError):
        await _run_scheduler_loop(runner, bot)

    runner.has_candidate_profile_data.assert_awaited_with("tenant", "123")
    runner.run_tenant.assert_not_called()
    runner.latest_jobs.assert_not_called()
    written_state_keys = [call.args[0] for call in store.set_run_state.call_args_list]
    assert "bot_scheduler:last_attempt_at" not in written_state_keys
    assert "bot_scheduler:last_run_emitted" not in written_state_keys
    assert "bot_scheduler:last_publish_sent" not in written_state_keys
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_scheduler_loop_resets_stale_publish_state_on_empty_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"sleep": 0}

    async def fake_sleep(_seconds: float) -> None:
        calls["sleep"] += 1
        if calls["sleep"] > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr("job_ftch.adapters.telegram_bot.main.asyncio.sleep", fake_sleep)

    store_state: dict[str, str] = {
        "bot_scheduler:last_run_emitted": "7",
        "bot_scheduler:last_publish_sent": "2",
        "bot_scheduler:last_publish_error": "new error",
    }

    async def _get_state(key: str) -> str | None:
        return store_state.get(key)

    async def _set_state(key: str, value: str) -> None:
        store_state[key] = value

    store = MagicMock()
    store.get_run_state = MagicMock(side_effect=_get_state)
    store.set_run_state = MagicMock(side_effect=_set_state)

    runner = MagicMock()
    runner.get_runtime.return_value = SimpleNamespace(
        settings=SimpleNamespace(
            bot_send_limit_per_run=15,
        ),
        store=store,
    )
    runner.tenant_ids.return_value = ["tenant"]
    runner.get_schedule_interval = MagicMock(return_value=asyncio.Future())
    runner.get_schedule_interval.return_value.set_result(1)
    runner.get_publish_channel = MagicMock(return_value=asyncio.Future())
    runner.get_publish_channel.return_value.set_result("@out")
    runner.get_publish_user_id = MagicMock(return_value=asyncio.Future())
    runner.get_publish_user_id.return_value.set_result("123")
    runner.has_candidate_profile_data = MagicMock(return_value=asyncio.Future())
    runner.has_candidate_profile_data.return_value.set_result(True)
    runner.run_tenant = MagicMock(return_value=asyncio.Future())
    runner.run_tenant.return_value.set_result(SimpleNamespace(emitted=0))
    runner.latest_jobs = MagicMock(return_value=asyncio.Future())
    runner.latest_jobs.return_value.set_result([])
    bot = MagicMock()

    with pytest.raises(asyncio.CancelledError):
        await _run_scheduler_loop(runner, bot)

    runner.latest_jobs.assert_not_called()
    bot.send_message.assert_called_once()
    assert bot.send_message.call_args.args[0] == "123"
    assert "Публикация пропущена: новых вакансий 0" in bot.send_message.call_args.args[1]
    assert store_state["bot_scheduler:last_run_emitted"] == "0"
    assert store_state["bot_scheduler:last_publish_sent"] == "0"
    assert store_state["bot_scheduler:last_publish_error"] == ""
    assert store_state["bot_scheduler:last_publish_skipped_reason"] == "no_new_jobs"
    assert "bot_scheduler:last_publish_skipped_at" in store_state


@pytest.mark.asyncio
async def test_scheduler_loop_resets_stale_publish_state_on_failed_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"sleep": 0}

    async def fake_sleep(_seconds: float) -> None:
        calls["sleep"] += 1
        if calls["sleep"] > 0:
            raise asyncio.CancelledError

    monkeypatch.setattr("job_ftch.adapters.telegram_bot.main.asyncio.sleep", fake_sleep)

    store_state: dict[str, str] = {
        "bot_scheduler:last_run_emitted": "7",
        "bot_scheduler:last_publish_sent": "2",
        "bot_scheduler:last_publish_error": "new error",
    }

    async def _get_state(key: str) -> str | None:
        return store_state.get(key)

    async def _set_state(key: str, value: str) -> None:
        store_state[key] = value

    store = MagicMock()
    store.get_run_state = MagicMock(side_effect=_get_state)
    store.set_run_state = MagicMock(side_effect=_set_state)

    runner = MagicMock()
    runner.get_runtime.return_value = SimpleNamespace(
        settings=SimpleNamespace(
            bot_send_limit_per_run=15,
        ),
        store=store,
    )
    runner.tenant_ids.return_value = ["tenant"]
    runner.get_schedule_interval = MagicMock(return_value=asyncio.Future())
    runner.get_schedule_interval.return_value.set_result(1)
    runner.get_publish_channel = MagicMock(return_value=asyncio.Future())
    runner.get_publish_channel.return_value.set_result("@out")
    runner.get_publish_user_id = MagicMock(return_value=asyncio.Future())
    runner.get_publish_user_id.return_value.set_result("123")
    runner.has_candidate_profile_data = MagicMock(return_value=asyncio.Future())
    runner.has_candidate_profile_data.return_value.set_result(True)
    runner.run_tenant = MagicMock(side_effect=RuntimeError("run failed"))
    runner.latest_jobs = MagicMock(return_value=asyncio.Future())
    runner.latest_jobs.return_value.set_result([])
    bot = MagicMock()

    with pytest.raises(asyncio.CancelledError):
        await _run_scheduler_loop(runner, bot)

    runner.latest_jobs.assert_not_called()
    bot.send_message.assert_called_once()
    assert bot.send_message.call_args.args[0] == "123"
    assert "Автозапуск упал" in bot.send_message.call_args.args[1]
    assert store_state["bot_scheduler:last_run_emitted"] == "0"
    assert store_state["bot_scheduler:last_publish_sent"] == "0"
    assert store_state["bot_scheduler:last_publish_error"] == ""
    assert store_state["bot_scheduler:last_error"] == "run failed"


def test_bot_publish_gate_requires_accept_decision() -> None:
    base_job = {
        "post_type": PostType.JOB_POSTING,
        "quality_score": 1.0,
        "best_score": 0.9,
    }

    assert pipeline.job_passes_bot_publish_gates(
        SimpleNamespace(**base_job, routing_decision=MatchDecision.ACCEPT),
    )
    assert pipeline.job_passes_bot_publish_gates(
        SimpleNamespace(**base_job, routing_decision="accept"),
    )

    for decision in (None, MatchDecision.REVIEW, MatchDecision.REJECT, "review", "reject"):
        assert not pipeline.job_passes_bot_publish_gates(
            SimpleNamespace(**base_job, routing_decision=decision),
        )

    # The adapter does not own a second quality/relevance policy.
    assert pipeline.job_passes_bot_publish_gates(
        SimpleNamespace(
            post_type=PostType.JOB_POSTING,
            routing_decision=MatchDecision.ACCEPT,
            quality_score=0.0,
            best_score=0.0,
        )
    )


@pytest.mark.asyncio
async def test_scheduler_loop_drains_pending_publish_window_on_zero_emit_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    iteration = {"outer": 0}

    async def fake_sleep(seconds: float) -> None:
        if seconds >= 60:
            iteration["outer"] += 1
            store_state.pop("bot_scheduler:last_attempt_at", None)
            store_state["bot_scheduler:last_publish_attempt_at"] = (
                "2020-01-01T00:00:00+00:00"
            )
            if iteration["outer"] >= 2:
                raise asyncio.CancelledError

    monkeypatch.setattr("job_ftch.adapters.telegram_bot.main.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(
        "job_ftch.adapters.telegram_bot.sender.format_vacancy_card",
        lambda job: f"card:{job.group_id}",
    )

    store_state: dict[str, str] = {}

    async def _get_state(key: str) -> str | None:
        return store_state.get(key)

    async def _set_state(key: str, value: str) -> None:
        store_state[key] = value

    store = MagicMock()
    store.get_run_state = MagicMock(side_effect=_get_state)
    store.set_run_state = MagicMock(side_effect=_set_state)

    jobs = [
        SimpleNamespace(
            group_id="group-1",
            post_type=PostType.JOB_POSTING,
            routing_decision=MatchDecision.ACCEPT,
            quality_score=1.0,
            best_score=0.9,
        ),
        SimpleNamespace(
            group_id="group-2",
            post_type=PostType.JOB_POSTING,
            routing_decision=MatchDecision.ACCEPT,
            quality_score=1.0,
            best_score=0.9,
        ),
    ]

    runner = MagicMock()
    runner.get_runtime.return_value = SimpleNamespace(
        settings=SimpleNamespace(
            bot_send_limit_per_run=15,
        ),
        store=store,
    )
    runner.tenant_ids.return_value = ["tenant"]
    runner.get_schedule_interval = AsyncMock(side_effect=[1, 1])
    runner.get_publish_channel = AsyncMock(side_effect=["@out", "@out"])
    runner.get_publish_user_id = AsyncMock(side_effect=["123", "123"])
    runner.has_candidate_profile_data = AsyncMock(return_value=True)
    runner.run_tenant = AsyncMock(
        side_effect=[SimpleNamespace(emitted=2), SimpleNamespace(emitted=0)]
    )
    runner.latest_jobs = AsyncMock(side_effect=[jobs, jobs])

    send_attempts = {"count": 0}

    owner_reports: list[str] = []

    async def send_message(channel: str, card: str, **_kwargs: object) -> None:
        if channel == "123":
            owner_reports.append(card)
            return None
        send_attempts["count"] += 1
        if send_attempts["count"] <= 4:
            raise TelegramRetryAfter(
                method=SendMessage(chat_id="@out", text="card"),
                message="flood",
                retry_after=1,
            )

    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=send_message)

    with pytest.raises(asyncio.CancelledError):
        await _run_scheduler_loop(runner, bot)

    assert send_attempts["count"] == 6
    assert len(owner_reports) == 1
    runner.run_tenant.assert_awaited_once()
    assert store_state["bot_scheduler:pending_publish_since"] == ""
    assert store_state["bot_scheduler:last_run_emitted"] == "2"
    assert store_state["bot_scheduler:last_publish_sent"] == "2"


@pytest.mark.asyncio
async def test_publish_ledger_prunes_to_last_500_ids() -> None:
    from job_ftch.application.publish_ledger import load_publish_ledger, persist_publish_ledger

    store_state: dict[str, str] = {}

    async def _get_state(key: str) -> str | None:
        return store_state.get(key)

    async def _set_state(key: str, value: str) -> None:
        store_state[key] = value

    store = MagicMock()
    store.get_run_state = MagicMock(side_effect=_get_state)
    store.set_run_state = MagicMock(side_effect=_set_state)

    ledger = [f"group-{index}" for index in range(501)]
    await persist_publish_ledger(store, ledger)

    persisted = await load_publish_ledger(store)
    assert len(persisted) == 500
    assert persisted[0] == "group-1"
    assert persisted[-1] == "group-500"


def test_polling_readiness_marker_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Healthcheck marker must only exist while polling is live."""
    import importlib

    marker = tmp_path / "ready"
    monkeypatch.setenv("JOB_FTCH_BOT_READY_FILE", str(marker))
    main = importlib.reload(importlib.import_module("job_ftch.adapters.telegram_bot.main"))
    try:
        assert not marker.exists()
        main._mark_polling_ready()
        assert marker.exists()
        main._clear_polling_ready()
        assert not marker.exists()
    finally:
        monkeypatch.delenv("JOB_FTCH_BOT_READY_FILE", raising=False)
        importlib.reload(main)


def test_build_dispatcher_can_be_called_more_than_once() -> None:
    """Handler routers are module-level singletons.

    aiogram refuses to attach a router that still points at a previous parent, so a
    factory that silently only works once turns a second bot - or a second assembly in
    one process - into a RuntimeError.
    """
    first = build_dispatcher(runner=MagicMock(), config=_config())
    second = build_dispatcher(runner=MagicMock(), config=_config())

    assert first is not second
    assert {router.name for router in first.sub_routers} == {
        router.name for router in second.sub_routers
    }


def test_build_dispatcher_registers_the_feedback_router() -> None:
    dispatcher = build_dispatcher(runner=MagicMock(), config=_config())

    assert "feedback" in {router.name for router in dispatcher.sub_routers}
