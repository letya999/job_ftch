"""Main entry point for the Telegram bot using aiogram 3.x."""

from __future__ import annotations

import asyncio
import contextlib
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from structlog.contextvars import bind_contextvars, reset_contextvars

from job_ftch.adapters.telegram_bot.handlers import (
    base,
    channel,
    examples,
    feedback,
    pipeline,
    schedule,
    sources,
)
from job_ftch.adapters.telegram_bot.handlers.feedback import build_feedback_markup
from job_ftch.adapters.telegram_bot.middlewares.auth import AuthMiddleware
from job_ftch.adapters.telegram_bot.middlewares.di import DIMiddleware
from job_ftch.adapters.telegram_bot.middlewares.throttling import ThrottlingMiddleware
from job_ftch.adapters.telegram_bot.sender import TelegramCardSender
from job_ftch.application.channel_publisher import publish_jobs
from job_ftch.application.vacancy_feedback import is_feedback_enabled

if TYPE_CHECKING:
    from job_ftch.adapters.telegram_bot.config import TelegramBotConfig
    from job_ftch.application.tenant_runner import TenantRunner

logger = structlog.get_logger(__name__)


BOT_DESCRIPTION = (
    "AI job search assistant: собирает вакансии из Telegram и карьерных сайтов, "
    "фильтрует их под ваш профиль и помогает улучшать выдачу примерами."
)
BOT_SHORT_DESCRIPTION = "AI-вакансии под ваш профиль из Telegram и карьерных сайтов."


READINESS_PATH = Path(
    os.environ.get("JOB_FTCH_BOT_READY_FILE", "/tmp/job_ftch_bot_ready")  # nosec B108
)


def _mark_polling_ready() -> None:
    """Publish a readiness marker for the container healthcheck.

    Startup takes ~60s (tenant load, ontology merge, embedding model). During
    that window the container is "up" while the bot answers nothing, which is
    exactly how the 2026-07-19 incident looked from the outside.
    """
    try:
        READINESS_PATH.parent.mkdir(parents=True, exist_ok=True)
        READINESS_PATH.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
    except OSError as exc:
        logger.warning("bot_readiness_marker_failed", path=str(READINESS_PATH), error=str(exc))


def _clear_polling_ready() -> None:
    with contextlib.suppress(OSError):
        READINESS_PATH.unlink(missing_ok=True)


def _log_scheduler_task_result(task: asyncio.Task[object]) -> None:
    """Make an unexpected scheduler-loop exit visible immediately."""
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        logger.error("scheduler_loop_terminated", error=str(exc), exc_info=exc)


async def _maybe_await(value: object) -> object:
    if asyncio.isfuture(value) or asyncio.iscoroutine(value):
        return await value
    return value


def _build_bot_commands() -> list[BotCommand]:
    return [
        BotCommand(command="start", description="Главное меню / Статус"),
        BotCommand(command="positive", description="Добавить подходящее резюме"),
        BotCommand(command="negative", description="Добавить НЕ подходящее резюме"),
        BotCommand(command="positive_job", description="Добавить подходящую вакансию (пример)"),
        BotCommand(command="negative_job", description="Добавить НЕ подходящую вакансию (пример)"),
        BotCommand(command="examples", description="Список моих примеров"),
        BotCommand(command="resumes", description="Список моих резюме"),
        BotCommand(command="vacancies", description="Список моих вакансий"),
        BotCommand(command="tenant", description="Выбрать tenant"),
        BotCommand(command="sources", description="Список источников (URL)"),
        BotCommand(command="run", description="Запустить поиск сейчас"),
        BotCommand(
            command="clear", description="Очистить историю — следующий запуск увидит всё заново"
        ),
        BotCommand(command="schedule", description="Настроить частоту автозапуска"),
        BotCommand(command="channel", description="Настроить канал публикации вакансий"),
        BotCommand(command="feedback", description="Обратная связь на опубликованные вакансии"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
    ]


async def _warn_on_dev_like_publish_settings(runner: TenantRunner) -> None:
    for tenant_id in runner.tenant_ids():
        runtime = runner.get_runtime(tenant_id)
        settings = getattr(runtime, "settings", None)
        if settings is None:
            continue
        if not (
            getattr(settings, "tracing_capture_payloads", False)
            and str(getattr(settings, "log_level", "")).upper() == "DEBUG"
        ):
            continue
        try:
            publish_channel = await runner.get_publish_channel(tenant_id)
        except Exception:
            continue
        if publish_channel:
            logger.warning(
                "dev-like settings with production publishing",
                tenant_id=tenant_id,
                publish_channel=publish_channel,
            )


async def configure_bot(bot: Bot, config: TelegramBotConfig | None = None) -> None:
    """Apply Bot API configuration needed at startup."""
    commands = _build_bot_commands()
    await bot.set_my_description(BOT_DESCRIPTION)
    await bot.set_my_short_description(BOT_SHORT_DESCRIPTION)
    await bot.set_my_commands(commands)
    logger.info(
        "telegram_bot_commands_registered",
        command_count=len(commands),
        commands=[command.command for command in commands],
    )


def build_bot(config: TelegramBotConfig) -> Bot:
    """Build aiogram Bot instance."""
    return Bot(token=config.token)


def build_dispatcher(
    runner: TenantRunner,
    config: TelegramBotConfig,
) -> Dispatcher:
    """Build aiogram Dispatcher instance with all routers and middlewares."""
    dp = Dispatcher(storage=MemoryStorage())

    # Register middlewares (order matters)
    # 1. DI middleware (injects runner, config, etc.)
    dp.update.outer_middleware(DIMiddleware(runner=runner, config=config))
    # 2. Auth middleware (checks if user is allowed)
    dp.update.outer_middleware(AuthMiddleware())
    # 3. Throttling middleware (only for messages)
    dp.message.middleware(ThrottlingMiddleware())

    # Handler modules expose one module-level Router each, and aiogram refuses to attach a
    # router that still points at a previous parent. Detaching first keeps this factory
    # callable more than once per process, which building a second bot - or a test that
    # asserts the assembly twice - otherwise turns into a RuntimeError.
    for router in (
        base.router,
        examples.router,
        sources.router,
        pipeline.router,
        schedule.router,
        channel.router,
        feedback.router,
    ):
        router._parent_router = None  # noqa: SLF001 - no public detach exists
        dp.include_router(router)

    return dp


async def _run_scheduler_loop(runner: TenantRunner, bot: Bot) -> None:
    """Background loop: periodically run the pipeline and publish new jobs to channel."""
    from job_ftch.adapters.telegram_bot.handlers.pipeline import (
        _active_runs,
        job_passes_bot_publish_gates,
        publish_candidate_fetch_limit,
    )

    while True:
        try:
            now = datetime.now(UTC)
            tenant_ids = list(runner.tenant_ids())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("scheduler_iteration_setup_failed", error=str(exc))
            _mark_polling_ready()
            await asyncio.sleep(60)
            continue

        for tenant_id in tenant_ids:
            run_result = None
            publish_context_tokens = None
            try:
                try:
                    interval = await runner.get_schedule_interval(tenant_id)
                except Exception:
                    interval = None

                if interval is None or interval <= 0:
                    continue

                last_run = None
                try:
                    last_attempt_raw = await _maybe_await(
                        runner.get_runtime(tenant_id).store.get_run_state(
                            "bot_scheduler:last_attempt_at"
                        )
                    )
                except Exception as exc:
                    logger.exception(
                        "scheduler_state_read_failed", tenant_id=tenant_id, error=str(exc)
                    )
                    continue
                if isinstance(last_attempt_raw, str) and last_attempt_raw:
                    with contextlib.suppress(ValueError):
                        parsed = datetime.fromisoformat(last_attempt_raw)
                        last_run = (
                            parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
                        )
                if last_run is not None and (now - last_run).total_seconds() < interval:
                    continue

                try:
                    publish_channel = await runner.get_publish_channel(tenant_id)
                    publish_user_id = await runner.get_publish_user_id(tenant_id)
                except Exception:
                    publish_channel = None
                    publish_user_id = None

                if not publish_channel:
                    continue

                if not publish_user_id:
                    logger.warning(
                        "scheduler_publish_owner_missing",
                        tenant_id=tenant_id,
                        channel=publish_channel,
                    )
                    continue

                try:
                    has_profile = await runner.has_candidate_profile_data(
                        tenant_id, str(publish_user_id)
                    )
                except Exception as exc:
                    logger.warning(
                        "scheduler_profile_check_failed",
                        tenant_id=tenant_id,
                        user_id=str(publish_user_id),
                        error=str(exc),
                    )
                    continue
                if not has_profile:
                    logger.info(
                        "scheduler_skipped_unconfigured_profile",
                        tenant_id=tenant_id,
                        user_id=str(publish_user_id),
                        channel=publish_channel,
                    )
                    continue

                if str(tenant_id) in _active_runs:
                    logger.info(
                        "scheduler_skipped_active_run",
                        tenant_id=tenant_id,
                        active=True,
                    )
                    continue

                # Run with the channel owner's profile so scheduled publishing matches
                # the same profile-aware filtering as manual /run.
                t_start = datetime.now(UTC)
                try:
                    await _maybe_await(
                        runner.get_runtime(tenant_id).store.set_run_state(
                            "bot_scheduler:last_attempt_at", t_start.isoformat()
                        )
                    )
                    await _maybe_await(
                        runner.get_runtime(tenant_id).store.set_run_state(
                            "bot_scheduler:last_run_emitted", "0"
                        )
                    )
                    await _maybe_await(
                        runner.get_runtime(tenant_id).store.set_run_state(
                            "bot_scheduler:last_publish_sent", "0"
                        )
                    )
                    await _maybe_await(
                        runner.get_runtime(tenant_id).store.set_run_state(
                            "bot_scheduler:last_publish_error", ""
                        )
                    )
                except Exception as exc:
                    logger.exception(
                        "scheduler_state_write_failed", tenant_id=tenant_id, error=str(exc)
                    )
                    continue
                try:
                    run_result = await runner.run_tenant(tenant_id, user_id=publish_user_id)
                except Exception as run_err:
                    try:
                        await _maybe_await(
                            runner.get_runtime(tenant_id).store.set_run_state(
                                "bot_scheduler:last_error", str(run_err)
                            )
                        )
                    except Exception:
                        logger.exception("scheduler_error_state_write_failed", tenant_id=tenant_id)
                    logger.exception(
                        "scheduler_loop_run_failed", tenant_id=tenant_id, error=str(run_err)
                    )
                    continue
                # Nothing ran (tenant lock held elsewhere): do not record a success
                # timestamp, or the scheduler would look healthy while idling.
                if getattr(run_result, "skipped_already_active", False) is True:
                    logger.info("scheduler_skipped_locked_tenant", tenant_id=tenant_id)
                    continue
                try:
                    await _maybe_await(
                        runner.get_runtime(tenant_id).store.set_run_state(
                            "bot_scheduler:last_success_at", datetime.now(UTC).isoformat()
                        )
                    )
                    emitted = getattr(run_result, "emitted", 0)
                    await _maybe_await(
                        runner.get_runtime(tenant_id).store.set_run_state(
                            "bot_scheduler:last_run_emitted", str(emitted)
                        )
                    )
                    await _maybe_await(
                        runner.get_runtime(tenant_id).store.set_run_state(
                            "bot_scheduler:last_error", ""
                        )
                    )
                except Exception as exc:
                    logger.exception(
                        "scheduler_success_state_write_failed",
                        tenant_id=tenant_id,
                        error=str(exc),
                    )
                    continue

                publish_context_tokens = bind_contextvars(
                    tenant_id=tenant_id,
                    source_run_id=str(getattr(run_result, "source_run_id", "") or ""),
                    graph_hash=str(getattr(run_result, "graph_hash", "") or ""),
                )
                persisted_candidates = 0
                eligible_to_send = 0
                chan_count = 0
                try:
                    pending_raw = await _maybe_await(
                        runner.get_runtime(tenant_id).store.get_run_state(
                            "bot_scheduler:pending_publish_since"
                        )
                    )
                    has_pending_publish = isinstance(pending_raw, str) and bool(pending_raw)
                    if emitted == 0 and not has_pending_publish:
                        continue

                    settings = getattr(runner.get_runtime(tenant_id), "settings", None)
                    store = runner.get_runtime(tenant_id).store
                    send_limit = getattr(settings, "bot_send_limit_per_run", 15)

                    # If the previous publish failed partway, extend the window back
                    # to that run's start so unsent jobs are retried instead of
                    # silently lost (a re-send of an already-posted card is the
                    # lesser evil than dropping fresh vacancies).
                    publish_since = t_start
                    if isinstance(pending_raw, str) and pending_raw:
                        with contextlib.suppress(ValueError):
                            pending_since = datetime.fromisoformat(pending_raw)
                            if pending_since.tzinfo is None:
                                pending_since = pending_since.replace(tzinfo=UTC)
                            publish_since = min(publish_since, pending_since)

                    jobs = await runner.latest_jobs(
                        tenant_id,
                        limit=publish_candidate_fetch_limit(send_limit),
                        since=publish_since,
                        user_id=publish_user_id,
                    )
                    persisted_candidates = len(jobs)

                    # Filter to publishable jobs; recency is already applied in latest_jobs().
                    new_jobs = [j for j in jobs if job_passes_bot_publish_gates(j)]
                    eligible_to_send = len(new_jobs)
                    if 0 < persisted_candidates < emitted:
                        logger.warning(
                            "bot_delivery_partial_loss",
                            delivery="scheduler",
                            tenant_id=tenant_id,
                            routing_accepted=emitted,
                            persisted_candidates=persisted_candidates,
                            eligible_to_send=eligible_to_send,
                            lost=emitted - persisted_candidates,
                        )
                    if emitted > 0 and not new_jobs:
                        logger.error(
                            "pipeline_delivery_contract_violation",
                            delivery="scheduler",
                            routing_accepted=emitted,
                            persisted_candidates=persisted_candidates,
                            eligible_to_send=eligible_to_send,
                        )

                    await _maybe_await(
                        store.set_run_state(
                            "bot_scheduler:last_publish_attempt_at",
                            datetime.now(UTC).isoformat(),
                        )
                    )
                    publish_outcome = await publish_jobs(
                        new_jobs,
                        target=publish_channel,
                        sender=TelegramCardSender(
                            bot,
                            markup_for=build_feedback_markup
                            if await is_feedback_enabled(store, tenant_id)
                            else None,
                        ),
                        store=store,
                        send_limit=send_limit,
                        # Passed explicitly (rather than relying on the default) so
                        # this module's asyncio.sleep stays the single patch point
                        # for driving the loop in tests.
                        sleep=asyncio.sleep,
                    )
                    chan_count = publish_outcome.sent
                    publish_error = publish_outcome.error or ""
                    had_flood_failure = publish_outcome.had_transient_failure
                    # Extend the retry window only for retryable (flood) failures.
                    # Permanently-bad cards must NOT pin the window, or every cycle
                    # would re-send the whole batch (duplicates).
                    if had_flood_failure:
                        await _maybe_await(
                            store.set_run_state(
                                "bot_scheduler:pending_publish_since",
                                publish_since.isoformat(),
                            )
                        )
                    else:
                        await _maybe_await(
                            store.set_run_state("bot_scheduler:pending_publish_since", "")
                        )
                    await _maybe_await(
                        store.set_run_state("bot_scheduler:last_publish_sent", str(chan_count))
                    )
                    if publish_error:
                        await _maybe_await(
                            store.set_run_state("bot_scheduler:last_publish_error", publish_error)
                        )
                    else:
                        await _maybe_await(
                            store.set_run_state(
                                "bot_scheduler:last_publish_success_at",
                                datetime.now(UTC).isoformat(),
                            )
                        )
                        await _maybe_await(
                            store.set_run_state("bot_scheduler:last_publish_error", "")
                        )

                    logger.info(
                        "scheduler_channel_published",
                        tenant_id=tenant_id,
                        channel=publish_channel,
                        sent=chan_count,
                        emitted=emitted,
                    )
                except Exception as pub_err:
                    try:
                        await _maybe_await(
                            runner.get_runtime(tenant_id).store.set_run_state(
                                "bot_scheduler:last_publish_error", str(pub_err)
                            )
                        )
                    except Exception:
                        logger.exception("scheduler_error_state_write_failed", tenant_id=tenant_id)
                    logger.exception(
                        "scheduler_publish_failed", tenant_id=tenant_id, error=str(pub_err)
                    )
                finally:
                    from job_ftch.infrastructure.observability.openobserve import (
                        record_bot_delivery_metrics,
                    )

                    try:
                        record_bot_delivery_metrics(
                            run_result,
                            persisted_candidates=persisted_candidates,
                            eligible_to_send=eligible_to_send,
                            chat_sent=0,
                            channel_posted=chan_count,
                        )
                    except Exception as exc:
                        logger.exception(
                            "scheduler_delivery_metrics_failed",
                            tenant_id=tenant_id,
                            error=str(exc),
                        )
                    try:
                        await _maybe_await(
                            runner.refresh_runtime_state_metrics(tenant_id, run_result)
                        )
                    except Exception as exc:
                        logger.exception(
                            "scheduler_runtime_metrics_failed",
                            tenant_id=tenant_id,
                            error=str(exc),
                        )
                    if publish_context_tokens is not None:
                        reset_contextvars(**publish_context_tokens)
            except asyncio.CancelledError:
                raise
            except Exception as pub_err:
                logger.exception(
                    "scheduler_iteration_failed", tenant_id=tenant_id, error=str(pub_err)
                )
            finally:
                with contextlib.suppress(Exception):
                    await _maybe_await(
                        runner.get_runtime(tenant_id).store.set_run_state(
                            "bot_scheduler:last_heartbeat_at", datetime.now(UTC).isoformat()
                        )
                    )

        _mark_polling_ready()
        await asyncio.sleep(60)


async def start_polling(
    runner: TenantRunner,
    config: TelegramBotConfig,
) -> None:
    """Start polling for updates."""
    from job_ftch.application.logging import configure_logging
    from job_ftch.config import get_settings
    from job_ftch.infrastructure.observability import configure_observability

    settings = get_settings()
    configure_logging(settings.log_level)
    configure_observability(settings)

    bot = build_bot(config)
    await configure_bot(bot, config)

    scheduler_task = asyncio.create_task(
        _run_scheduler_loop(runner, bot),
        name="bot_scheduler_loop",
    )
    scheduler_task.add_done_callback(_log_scheduler_task_result)

    # Auth sanity warnings
    if config.open_access:
        logger.warning("telegram_bot_open_access_enabled: bot accepts updates from ANY user")
    if not config.open_access and not config.allowed_user_ids and not config.admin_user_ids:
        logger.warning(
            "telegram_bot_auth_deny_all: no allowlist configured and open_access=False"
            " — all users will be silently blocked"
        )
    dp = build_dispatcher(runner=runner, config=config)

    logger.info("telegram_bot_polling_started")
    _mark_polling_ready()
    try:
        await _warn_on_dev_like_publish_settings(runner)
        await dp.start_polling(bot, drop_pending_updates=False)
    finally:
        _clear_polling_ready()
        scheduler_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scheduler_task
        await bot.session.close()
