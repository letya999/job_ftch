import asyncio
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

from adapters.telegram_bot.formatter import format_vacancy_card
from job_ftch.domain.models import PostType

if TYPE_CHECKING:
    from job_ftch.application.tenant_runner import TenantRunner

logger = structlog.get_logger(__name__)
router = Router(name="pipeline")


def _host_resolves_to_blocked_ip(hostname: str | None) -> bool:
    """True if hostname resolves to a loopback/private/link-local/reserved/unspecified IP.

    Used as an SSRF guard before issuing outbound liveness requests against URLs that
    originate from untrusted scraped content.
    """
    if not hostname:
        return True
    try:
        infos = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, UnicodeError, ValueError):
        # Unresolvable host -> treat as not-alive (caller returns False), don't connect.
        return True
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return True
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return True
    return False


async def _url_is_alive(url: str | None) -> bool:
    """Quick HEAD check for non-Telegram URLs. Returns True for Telegram URLs without checking.

    SSRF-guarded: URLs come from untrusted scraped content, so the resolved host is validated
    against private/loopback/link-local/reserved ranges before any outbound request, and
    redirects are NOT followed (a public host must not bounce us into the internal network).
    """
    if not url:
        return False
    url_str = str(url)
    # Telegram links can't be HEAD-checked without auth, assume alive
    if "t.me" in url_str or "telegram.me" in url_str:
        return True
    parsed = urlparse(url_str)
    if parsed.scheme not in ("http", "https"):
        return False
    if _host_resolves_to_blocked_ip(parsed.hostname):
        return False
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(
                url_str,
                timeout=aiohttp.ClientTimeout(total=3),
                allow_redirects=False,
            ) as resp:
                # 2xx/3xx = reachable; 3xx is treated as alive without following the hop.
                return resp.status < 400
    except Exception:
        return False


async def run_pipeline_for_chat(message: Message, runner: "TenantRunner", bot: Bot, user_id_override: int | None = None) -> None:
    tenant_id = runner.default_tenant_id()
    t0 = time.monotonic()
    status_msg = await message.answer("🚀 Запускаю пайплайн...\nЭто может занять 1–3 минуты.")

    try:
        run_result = await runner.run_tenant(tenant_id)
    except Exception as exc:
        logger.exception("pipeline_run_failed", error=str(exc))
        await status_msg.edit_text(f"❌ Ошибка запуска: {exc}")
        return

    duration = int(time.monotonic() - t0)

    # Pull stats from RunSummary
    fetched = getattr(run_result, "fetched", 0)
    triaged = getattr(run_result, "triaged", 0)
    extracted = getattr(run_result, "extracted", 0)
    duplicates = getattr(run_result, "duplicates", 0)
    dropped = getattr(run_result, "dropped", 0)
    emitted = getattr(run_result, "emitted", 0)
    failed = getattr(run_result, "failed", 0)
    drop_reasons: dict[str, int] = getattr(run_result, "drop_reasons", {}) or {}

    # Approximate cost: gpt-4o-mini ~$0.002/call
    cost_estimate = extracted * 0.002

    funnel_parts = [
        f"📥 Получено:         {fetched}",
        f"🚫 Отфильтровано:    {dropped - duplicates} (не-вакансии)",
        f"🔄 Дубликаты:        {duplicates}",
        f"🤖 LLM обработано:   {extracted}",
        f"⭐ Найдено вакансий: {emitted}",
    ]
    funnel_text = "\n".join(funnel_parts)

    # Add drop reason highlights if notable
    notable_reasons = {
        k: v for k, v in drop_reasons.items()
        if v > 0 and k in ("telegram_low_signal", "job_out_of_scope", "irrelevant_content", "duplicate_content")
    }
    if notable_reasons:
        reasons_str = ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in notable_reasons.items())
        funnel_text += f"\n<i>Причины дропа: {reasons_str}</i>"

    footer = f"⏱ {duration}с"
    if extracted > 0:
        footer += f"  •  ~${cost_estimate:.3f}"
    if failed > 0:
        footer += f"  •  ошибок: {failed}"

    if emitted == 0:
        await status_msg.edit_text(
            f"✅ Готово  {footer}\n\n{funnel_text}\n\n"
            "Ничего не найдено.\n\n"
            "Попробуй:\n• Добавить больше примеров /positive\n• Расширить источники /sources",
            parse_mode="HTML"
        )
        return

    await status_msg.edit_text(
        f"✅ Готово  {footer}\n\n{funnel_text}\n\nОтправляю вакансии 👇",
        parse_mode="HTML"
    )

    # Fetch and send jobs
    try:
        resolved_uid = user_id_override if user_id_override is not None else (message.from_user.id if message.from_user else None)
        user_id = str(resolved_uid) if resolved_uid is not None else "0"
        
        # Get limits from runner settings (with fallback for compatibility)
        _settings = getattr(runner, "settings", None)
        send_limit = getattr(_settings, "bot_send_limit_per_run", 15)
        min_quality = getattr(_settings, "bot_min_quality_score", 0.0)
        min_relevance = getattr(_settings, "bot_min_relevance_score", 0.0)

        jobs_raw = await runner.latest_jobs(
            tenant_id, 
            limit=send_limit * 4, 
            user_id=user_id, 
            min_score=min_relevance
        )

        valid_jobs = []
        for j in jobs_raw:
            pt = j.post_type
            sk = str(j.source_kind)
            quality = j.quality_score if j.quality_score is not None else 1.0
            best = j.best_score if j.best_score is not None else 0.0

            # Relevance floor (defensive check on top of latest_jobs filtering)
            if best < min_relevance:
                continue

            # Quality gate
            if quality < min_quality:
                continue

            # Post-type gate
            if pt == PostType.JOB_POSTING:
                valid_jobs.append(j)

        jobs = valid_jobs[:send_limit]

        if not jobs:
            await message.answer("Нет подходящих вакансий. Попробуй /clear и /run заново, или добавь больше примеров /positive")
            return

        sent_count = 0
        for job in jobs:
            if not await _url_is_alive(job.canonical_url):
                logger.info("vacancy_url_dead", url=str(job.canonical_url), title=job.title)
                continue
            card = format_vacancy_card(job)
            await message.answer(card, parse_mode="HTML", disable_web_page_preview=True)
            sent_count += 1
            await asyncio.sleep(0.3)

        await status_msg.edit_text(
            f"✅ Готово  {footer}\n\n{funnel_text}\n\n✉️ Отправлено: {sent_count}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.exception("failed_to_send_jobs", error=str(e))
        await message.answer(f"❌ Ошибка при получении вакансий: {e}")


@router.message(Command("run"))
async def cmd_run(message: Message, runner: "TenantRunner", bot: Bot) -> None:
    await run_pipeline_for_chat(message, runner, bot)


@router.message(Command("clear"))
async def cmd_clear(message: Message, runner: "TenantRunner", bot: Bot) -> None:
    tenant_id = runner.default_tenant_id()
    try:
        dedup, groups = await runner.clear_all(tenant_id)
        await message.answer(
            f"🗑 Очищено: дедуп {dedup}, групп вакансий {groups}.\n"
            "База полностью сброшена. Следующий /run увидит всё заново."
        )
    except Exception as e:
        logger.exception("clear_all_failed", error=str(e))
        await message.answer(f"❌ Ошибка очистки: {e}")
