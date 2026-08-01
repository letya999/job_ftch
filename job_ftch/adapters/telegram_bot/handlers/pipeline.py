import contextlib
import inspect
import ipaddress
import socket
import time
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import aiohttp
import structlog
from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message
from aiohttp.abc import AbstractResolver, ResolveResult
from opentelemetry import context as otel_context
from opentelemetry import trace
from structlog.contextvars import bind_contextvars, reset_contextvars

from job_ftch.adapters.telegram_bot.formatter import resolve_job_url
from job_ftch.adapters.telegram_bot.handlers.feedback import build_feedback_markup
from job_ftch.adapters.telegram_bot.sender import ReplyCardSender, TelegramCardSender
from job_ftch.adapters.telegram_bot.utils import safe_error_reply
from job_ftch.application.channel_publisher import publish_jobs
from job_ftch.application.run_report import (
    build_runtime_run_report,
    render_runtime_run_footer,
    render_runtime_run_report_text,
    split_drop_buckets,
)
from job_ftch.application.vacancy_feedback import is_feedback_enabled
from job_ftch.domain.models import MatchDecision, PostType

_active_runs: set[str] = set()

if TYPE_CHECKING:
    from collections.abc import Mapping
    from contextvars import Token
    from typing import Any

    from job_ftch.application.tenant_runner import TenantRunner
    from job_ftch.domain import JobRecord

logger = structlog.get_logger(__name__)
router = Router(name="pipeline")
_BOT_PUBLISH_FETCH_MULTIPLIER = 20
_BOT_PUBLISH_FETCH_FLOOR = 100
_TELEGRAM_LIVENESS_HOSTS = frozenset({"t.me", "telegram.me"})


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _resolve_allowed_addresses(hostname: str | None, port: int) -> tuple[ResolveResult, ...]:
    if not hostname:
        return ()
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, ValueError):
        # Unresolvable host -> treat as not-alive (caller returns False), don't connect.
        return ()

    resolved: list[ResolveResult] = []
    for family, _socktype, proto, _canonname, sockaddr in infos:
        ip_str = str(sockaddr[0])
        if _is_blocked_ip(ip_str):
            return ()
        resolved.append(
            {
                "hostname": hostname,
                "host": ip_str,
                "port": int(sockaddr[1]) if len(sockaddr) > 1 else port,
                "family": family,
                "proto": proto,
                "flags": 0,
            }
        )
    return tuple(resolved)


class _PinnedHostResolver(AbstractResolver):
    """aiohttp resolver that prevents a second DNS lookup from changing the peer IP."""

    def __init__(self, hostname: str, addresses: tuple[ResolveResult, ...]) -> None:
        self._hostname = hostname
        self._addresses = addresses

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ) -> list[ResolveResult]:
        del port, family
        if host != self._hostname:
            raise OSError("unexpected host during pinned URL liveness resolution")
        return list(self._addresses)

    async def close(self) -> None:
        return None


def _is_telegram_liveness_url(parsed: object) -> bool:
    hostname = getattr(parsed, "hostname", None)
    if not hostname:
        return False
    return str(hostname).rstrip(".").lower() in _TELEGRAM_LIVENESS_HOSTS


def _host_resolves_to_blocked_ip(hostname: str | None) -> bool:
    """True if hostname resolves to a loopback/private/link-local/reserved/unspecified IP.

    Used as an SSRF guard before issuing outbound liveness requests against URLs that
    originate from untrusted scraped content.
    """
    return not _resolve_allowed_addresses(hostname, 0)


async def _url_is_alive(url: str | None) -> bool:
    """Quick HEAD check for non-Telegram URLs. Returns True for Telegram URLs without checking.

    SSRF-guarded: URLs come from untrusted scraped content, so the resolved host is validated
    against private/loopback/link-local/reserved ranges before any outbound request, and
    redirects are NOT followed (a public host must not bounce us into the internal network).
    """
    if not url:
        return False
    url_str = str(url)
    parsed = urlparse(url_str)
    # Telegram links can't be HEAD-checked without auth, assume alive.
    if _is_telegram_liveness_url(parsed):
        return True
    if parsed.scheme not in ("http", "https"):
        return False
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return False
    addresses = _resolve_allowed_addresses(parsed.hostname, port)
    if not addresses or not parsed.hostname:
        return False
    resolver = _PinnedHostResolver(parsed.hostname, addresses)
    connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False)
    try:
        async with (
            aiohttp.ClientSession(connector=connector) as session,
            session.head(
                url_str,
                timeout=aiohttp.ClientTimeout(total=3),
                allow_redirects=False,
            ) as resp,
        ):
            # Career sites commonly reject HEAD with 401/403/405 while the
            # vacancy page itself is valid. Only explicit terminal absence is
            # strong enough to hide an already accepted pipeline result.
            return resp.status not in {404, 410}
    except Exception:
        with contextlib.suppress(Exception):
            await connector.close()
        # Delivery is fail-open: a transient DNS/timeout must not become a
        # second policy gate after DecisionNode accepted the vacancy.
        return True


def job_passes_bot_publish_gates(
    job: object,
) -> bool:
    """Return True when the policy owner accepted a well-formed vacancy.

    Relevance and quality belong to DecisionNode.  Reapplying numeric
    thresholds here created a second, undocumented decision policy and caused
    accepted jobs to disappear between the pipeline summary and Telegram.
    """
    if getattr(job, "post_type", None) != PostType.JOB_POSTING:
        return False
    decision = getattr(job, "routing_decision", None)
    if isinstance(decision, MatchDecision):
        if decision is not MatchDecision.ACCEPT:
            return False
    elif str(decision).lower() != MatchDecision.ACCEPT.value:
        return False
    return True


def publish_candidate_fetch_limit(send_limit: int) -> int:
    """Fetch a wider pool than the final send cap to survive gating and dead-link skips."""
    return max(
        send_limit * _BOT_PUBLISH_FETCH_MULTIPLIER, send_limit + 20, _BOT_PUBLISH_FETCH_FLOOR
    )


def _split_drop_buckets(drop_reasons: "Mapping[str, int]") -> tuple[int, int, int]:
    """Separate "already in the database" drops from genuine non-vacancy drops.

    Both used to be reported as "не-вакансии", so a run whose candidates were
    all seen before looked identical to a run whose filter rejected everything.
    """
    buckets = split_drop_buckets(drop_reasons)
    return buckets.already_seen, buckets.non_vacancy, buckets.low_relevance + buckets.other


def _format_runtime_timestamp(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    return value.strip().replace("T", " ")


async def run_pipeline_for_chat(
    message: Message, runner: "TenantRunner", bot: Bot, user_id_override: int | None = None
) -> None:
    resolved_uid = (
        user_id_override
        if user_id_override is not None
        else (message.from_user.id if message.from_user else None)
    )
    user_id = str(resolved_uid) if resolved_uid is not None else "0"
    tenant_id = await runner.get_selected_tenant_id(user_id)

    # Require user-provided profile examples before running the pipeline.
    try:
        has_profile = await runner.has_candidate_profile_data(tenant_id, user_id)
    except Exception:
        has_profile = False
    if not has_profile:
        await message.answer(
            "❌ Профиль не настроен.\n\n"
            "Чтобы я понял, какие вакансии подходят, добавь примеры:\n\n"
            "• /positive — вставь текст подходящей вакансии или резюме\n"
            "• /negative — вставь текст неподходящей (необязательно)\n\n"
            "Затем снова /run."
        )
        return

    if tenant_id in _active_runs:
        await message.answer("⏳ Пайплайн уже запущен. Подожди окончания.")
        return

    _active_runs.add(tenant_id)
    t0 = time.monotonic()
    tracer = trace.get_tracer("adapters.telegram_bot.pipeline")
    bot_span = tracer.start_span("bot.run")
    otel_token = otel_context.attach(trace.set_span_in_context(bot_span))
    log_context_tokens: Mapping[str, Token[Any]] | None = None
    run_result = None
    persisted_candidates = 0
    eligible_to_send = 0
    sent_count = 0
    channel_posted = 0
    delivery_status = "started"
    chat_target_unusable = False
    chat_transient_failure = False
    channel_target_unusable = False
    channel_transient_failure = False
    try:
        status_msg = await message.answer(
            "🚀 Запускаю пайплайн...\nОбычно 3–8 минут, на холодном старте дольше."
        )
        try:
            run_result = await runner.run_tenant(tenant_id, user_id=user_id)
        except Exception as exc:
            delivery_status = "pipeline_failed"
            bot_span.record_exception(exc)
            await safe_error_reply(message, exc, "pipeline_run_failed")
            return

        # The tenant lock was held elsewhere, so nothing ran. Reporting the
        # empty summary as a finished run tnew the user "nothing found" for
        # work that never happened. Compared against True rather than for
        # truthiness: RunSummary sets a real bool, and a loose check would fire
        # on any duck-typed summary that merely exposes the attribute.
        if getattr(run_result, "skipped_already_active", False) is True:
            delivery_status = "skipped_already_active"
            await status_msg.edit_text(
                "⏳ Запуск для этого tenant уже идёт (другой процесс или расписание).\n"
                "Дождись окончания и повтори /run."
            )
            return

        source_run_id = str(getattr(run_result, "source_run_id", "") or "")
        graph_hash = str(getattr(run_result, "graph_hash", "") or "")
        log_context_tokens = bind_contextvars(
            tenant_id=tenant_id,
            source_run_id=source_run_id,
            graph_hash=graph_hash,
        )
        bot_span.set_attribute("job_ftch.source_run_id", source_run_id)
        bot_span.set_attribute("job_ftch.tenant_id", tenant_id)
        bot_span.set_attribute("job_ftch.graph_hash", graph_hash)

        duration = int(time.monotonic() - t0)

        report = build_runtime_run_report(run_result, duration_seconds=duration)
        emitted = report.emitted
        already_seen = report.drop_buckets.already_seen
        funnel_text = render_runtime_run_report_text(report)

        # A run drained by dedup measures the not-yet-seen residue, not the
        # selection quality. Saying so prevents reading it as a filter failure.
        if emitted == 0 and already_seen > 0 and report.seen_dominates_drops:
            funnel_text += (
                "\n<i>Почти всё уже есть в базе. Для честного замера "
                "сбрось базу и запусти /run заново.</i>"
            )
            with contextlib.suppress(Exception):
                scheduler_status = await runner.get_bot_scheduler_status(tenant_id)
                last_publish_sent = str(scheduler_status.get("last_publish_sent") or "").strip()
                last_publish_at = _format_runtime_timestamp(
                    scheduler_status.get("last_publish_success_at")
                )
                if last_publish_sent or last_publish_at:
                    publish_hint_parts = []
                    if last_publish_sent:
                        publish_hint_parts.append(f"отправлено {last_publish_sent}")
                    if last_publish_at:
                        publish_hint_parts.append(f"последняя публикация {last_publish_at}")
                    funnel_text += (
                        "\n<i>Проверь канал: автопубликация уже проходила ("
                        + ", ".join(publish_hint_parts)
                        + ").</i>"
                    )

        footer = render_runtime_run_footer(report)

        if emitted == 0:
            delivery_status = "no_routing_accepts"
            await status_msg.edit_text(
                f"✅ Готово  {footer}\n\n{funnel_text}\n\n"
                "Ничего не найдено.\n\n"
                "Попробуй:\n• Добавить больше примеров /positive\n• Расширить источники /sources",
                parse_mode="HTML",
            )
            return

        await status_msg.edit_text(
            f"✅ Готово  {footer}\n\n{funnel_text}\n\nОтправляю вакансии 👇", parse_mode="HTML"
        )

        _settings = getattr(runner.get_runtime(tenant_id), "settings", None)
        send_limit = getattr(_settings, "bot_send_limit_per_run", 15)
        jobs_raw = await runner.latest_jobs(
            tenant_id,
            limit=publish_candidate_fetch_limit(send_limit),
            since=getattr(run_result, "started_at", None),
            user_id=user_id,
        )
        persisted_candidates = len(jobs_raw)
        valid_jobs = [j for j in jobs_raw if job_passes_bot_publish_gates(j)]
        eligible_to_send = len(valid_jobs)
        logger.info(
            "bot_delivery_candidates",
            routing_accepted=emitted,
            persisted_candidates=persisted_candidates,
            eligible_to_send=eligible_to_send,
        )
        # Total loss is reported below. Partial loss used to pass silently: a run
        # that accepted 63 and delivered 59 looked identical to a healthy one.
        if 0 < persisted_candidates < emitted:
            logger.warning(
                "bot_delivery_partial_loss",
                delivery="chat",
                routing_accepted=emitted,
                persisted_candidates=persisted_candidates,
                eligible_to_send=eligible_to_send,
                lost=emitted - persisted_candidates,
            )
        if not valid_jobs:
            delivery_status = "persistence_contract_violation"
            logger.error(
                "pipeline_delivery_contract_violation",
                routing_accepted=emitted,
                persisted_candidates=persisted_candidates,
                eligible_to_send=eligible_to_send,
            )
            await status_msg.edit_text(
                f"✅ Готово  {footer}\n\n{funnel_text}\n\n"
                "⚠️ Вакансии приняты пайплайном, но не появились в выдаче. "
                "Ошибка сохранения уже записана в диагностику; повторная очистка базы не требуется.",
                parse_mode="HTML",
            )
            return

        live_jobs: list[JobRecord] = []
        for job in valid_jobs:
            if len(live_jobs) >= send_limit:
                break
            job_url = resolve_job_url(job)
            if not await _url_is_alive(job_url):
                logger.info("vacancy_url_dead", url=job_url, title=job.title)
                continue
            live_jobs.append(job)

        chat_outcome = await publish_jobs(
            live_jobs,
            target="chat",
            sender=ReplyCardSender(message),
            send_limit=send_limit,
            throttle_seconds=0.3,
        )
        delivered_jobs = list(chat_outcome.delivered)
        sent_count = chat_outcome.sent
        delivery_error = chat_outcome.error
        chat_target_unusable = chat_outcome.target_unusable
        chat_transient_failure = chat_outcome.had_transient_failure
        if delivery_error:
            logger.warning("chat_delivery_failed", tenant_id=tenant_id, error=delivery_error)

        if sent_count == 0:
            if delivery_error:
                if chat_transient_failure:
                    delivery_status = "chat_delivery_deferred"
                    await status_msg.edit_text(
                        f"✅ Готово  {footer}\n\n{funnel_text}\n\n"
                        "⏳ Вакансии найдены, но отправка в чат временно отложена из-за лимитов Telegram.",
                        parse_mode="HTML",
                    )
                else:
                    delivery_status = "chat_delivery_failed"
                    await status_msg.edit_text(
                        f"✅ Готово  {footer}\n\n{funnel_text}\n\n"
                        "⚠️ Вакансии найдены, но не удалось отправить их в чат.\n"
                        f"Ошибка: {delivery_error}",
                        parse_mode="HTML",
                    )
            else:
                delivery_status = "all_links_unavailable"
                await status_msg.edit_text(
                    f"✅ Готово  {footer}\n\n{funnel_text}\n\n"
                    "⚠️ Найдены вакансии, но все ссылки недоступны.\n"
                    "Попробуй /run позже или /clear чтобы обновить базу.",
                    parse_mode="HTML",
                )
            return

        delivery_status = "chat_delivery_partial" if delivery_error else "chat_delivered"

        if delivery_error:
            if chat_transient_failure:
                delivery_suffix = "\n⏳ Дальнейшая отправка приостановлена из-за лимитов Telegram. Остальные вакансии в очереди."
            elif chat_target_unusable:
                delivery_suffix = (
                    "\n⚠️ Дальнейшая отправка невозможна: бот заблокирован или чат недоступен."
                )
            else:
                delivery_suffix = f"\n⚠️ Дальше отправка остановилась: {delivery_error}"
        else:
            delivery_suffix = ""

        await status_msg.edit_text(
            f"✅ Готово  {footer}\n\n{funnel_text}\n\n✉️ Отправлено: {sent_count}{delivery_suffix}",
            parse_mode="HTML",
        )

        publish_channel: str | None = None
        try:
            publish_channel = await runner.get_publish_channel(tenant_id)
            if publish_channel and sent_count > 0:
                channel_store = runner.get_runtime(tenant_id).store
                channel_outcome = await publish_jobs(
                    delivered_jobs,
                    target=publish_channel,
                    sender=TelegramCardSender(
                        bot,
                        markup_for=build_feedback_markup
                        if await is_feedback_enabled(channel_store, tenant_id)
                        else None,
                    ),
                    store=channel_store,
                    send_limit=send_limit,
                )
                chan_count = channel_outcome.sent
                publish_error: str | None = channel_outcome.error
                channel_target_unusable = channel_outcome.target_unusable
                channel_transient_failure = channel_outcome.had_transient_failure
                if channel_target_unusable:
                    await message.answer(
                        f"⚠️ Не удалось опубликовать в {publish_channel}.\n"
                        "Убедитесь, что бот добавлен как администратор канала с правом публикации."
                    )
                if chan_count > 0:
                    channel_posted = chan_count
                    delivery_status = (
                        "channel_delivery_partial" if publish_error else "channel_delivered"
                    )
                    if publish_error:
                        if channel_transient_failure:
                            suffix = "\n⏳ Публикация приостановлена из-за лимитов Telegram."
                        elif channel_target_unusable:
                            suffix = "\n⚠️ Публикация невозможна: бот заблокирован или нет прав."
                        else:
                            suffix = f"\n⚠️ Дальше публикация остановилась: {publish_error}"
                    else:
                        suffix = ""
                    await message.answer(
                        f"📢 Опубликовано в {publish_channel}: {chan_count} вакансий.{suffix}"
                    )
                elif publish_error:
                    if channel_transient_failure:
                        await message.answer(
                            f"⏳ Очередь публикации для {publish_channel} ждет снятия лимитов Telegram."
                        )
                    else:
                        await message.answer(
                            f"⚠️ Публикация в {publish_channel} не удалась: {publish_error}"
                        )
        except Exception as e:
            logger.exception("channel_publish_error", error=str(e))
            channel_hint = f" в {publish_channel}" if publish_channel else ""
            await message.answer(
                f"⚠️ Публикация{channel_hint} не удалась. Проверь /channel и права бота."
            )
    except Exception as e:
        delivery_status = "post_processing_failed"
        bot_span.record_exception(e)
        await safe_error_reply(message, e, "pipeline_post_processing_failed")
    finally:
        if run_result is not None:
            bot_span.set_attribute(
                "job_ftch.routing_accepted", int(getattr(run_result, "emitted", 0) or 0)
            )
            bot_span.set_attribute("job_ftch.persisted_candidates", persisted_candidates)
            bot_span.set_attribute("job_ftch.eligible_to_send", eligible_to_send)
            bot_span.set_attribute("job_ftch.chat_sent", sent_count)
            bot_span.set_attribute("job_ftch.channel_posted", channel_posted)
            bot_span.set_attribute("job_ftch.delivery_status", delivery_status)
            logger.info(
                "bot_delivery_complete",
                routing_accepted=int(getattr(run_result, "emitted", 0) or 0),
                persisted_candidates=persisted_candidates,
                eligible_to_send=eligible_to_send,
                chat_sent=sent_count,
                channel_posted=channel_posted,
                delivery_status=delivery_status,
            )
            from job_ftch.infrastructure.observability.openobserve import (
                record_bot_delivery_metrics,
            )

            record_bot_delivery_metrics(
                run_result,
                persisted_candidates=persisted_candidates,
                eligible_to_send=eligible_to_send,
                chat_sent=sent_count,
                channel_posted=channel_posted,
                chat_target_unusable=chat_target_unusable,
                channel_target_unusable=channel_target_unusable,
                chat_transient_failure=chat_transient_failure,
                channel_transient_failure=channel_transient_failure,
            )
            refresh_result = runner.refresh_runtime_state_metrics(tenant_id, run_result)
            if inspect.isawaitable(refresh_result):
                await refresh_result
        if log_context_tokens is not None:
            reset_contextvars(**log_context_tokens)
        otel_context.detach(otel_token)
        bot_span.end()
        _active_runs.discard(tenant_id)


@router.message(Command("run"))
async def cmd_run(message: Message, runner: "TenantRunner", bot: Bot) -> None:
    await run_pipeline_for_chat(message, runner, bot)


@router.message(Command("clear"))
async def cmd_clear(message: Message, runner: "TenantRunner", bot: Bot) -> None:
    from job_ftch.application.tenant_locks import tenant_run_is_active

    user_id = str(message.from_user.id) if message.from_user else "0"
    tenant_id = await runner.get_selected_tenant_id(user_id)

    # `clear_all` drops dedup state, job groups and the vector collection. Doing
    # that under a live run corrupts the run in flight: the pipeline reads and
    # writes the very state being deleted.
    if tenant_id in _active_runs:
        await message.answer(
            "⏳ Пайплайн сейчас работает — очистка отменена.\n"
            "Дождись окончания /run и повтори /clear."
        )
        return
    try:
        settings = getattr(runner.get_runtime(tenant_id), "settings", None)
        if settings is not None and tenant_run_is_active(settings, tenant_id):
            await message.answer(
                "⏳ Для этого tenant уже идёт запуск (другой процесс) — очистка отменена.\n"
                "Дождись окончания и повтори /clear."
            )
            return
    except Exception:
        # The guard is advisory; a lookup failure must not block a legitimate clear.
        logger.warning("clear_active_run_check_failed", tenant_id=tenant_id)
    try:
        counts = await runner.clear_run_data(tenant_id)
        dedup = int(counts.get("dedup_records", 0))
        jobs = int(counts.get("jobs", 0))
        groups = int(counts.get("job_groups", 0))
        vectors = int(counts.get("vectors", 0))
        snapshots = int(counts.get("snapshots", 0))
        ingest_states = int(counts.get("source_ingest_states", 0))
        outbox = int(counts.get("outbox", 0))

        msg = (
            f"🗑 Очищено: дедуп {dedup}, джобов {jobs}, групп {groups}, снапшотов {snapshots}, "
            f"source state {ingest_states}"
        )
        if outbox > 0:
            msg += f", outbox {outbox}"
        if vectors > 0:
            msg += f", векторов {vectors}"
        msg += ".\nСледующий /run стартует с чистого run-state и увидит всё заново."

        try:
            publish_channel = await runner.get_publish_channel(tenant_id)
            if publish_channel:
                msg += (
                    f"\n\n⚠️ Канал {publish_channel} настроен. "
                    "Следующий /run опубликует все найденные вакансии заново."
                )
        except Exception:
            pass

        await message.answer(msg)
    except Exception as e:
        await safe_error_reply(message, e, "clear_all_failed")
