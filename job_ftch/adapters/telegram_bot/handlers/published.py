"""Read-only Telegram view of the published vacancy catalog."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram import Router
from aiogram.filters import Command

from job_ftch.adapters.telegram_bot.formatter import format_vacancy_card
from job_ftch.adapters.telegram_bot.handlers.pipeline import job_passes_bot_publish_gates
from job_ftch.adapters.telegram_bot.utils import require_admin_message
from job_ftch.application.publish_ledger import (
    extract_publish_canonical_url,
    extract_publish_job_id,
    load_publish_ledger,
    load_publish_url_ledger,
)

if TYPE_CHECKING:
    from aiogram.types import Message

    from job_ftch.adapters.telegram_bot.config import TelegramBotConfig
    from job_ftch.application.tenant_runner import TenantRunner

router = Router(name="published")
_MAX_PUBLISHED = 8


@router.message(Command("published"))
async def cmd_published(message: Message, runner: TenantRunner, config: TelegramBotConfig) -> None:
    if not await require_admin_message(message, config):
        return
    user_id = str(message.from_user.id) if message.from_user else None
    tenant_id = await runner.get_selected_tenant_id(user_id)
    jobs = await runner.latest_jobs(tenant_id, limit=_MAX_PUBLISHED, user_id=user_id)
    runtime = runner.get_runtime(tenant_id)
    published_ids = set(await load_publish_ledger(runtime.store))
    published_urls = set(await load_publish_url_ledger(runtime.store))
    jobs = [
        job
        for job in jobs
        if job_passes_bot_publish_gates(job)
        and (
            extract_publish_job_id(job) in published_ids
            or extract_publish_canonical_url(job) in published_urls
        )
    ]
    if not jobs:
        await message.answer("Опубликованных вакансий пока нет.")
        return
    body = "\n\n".join(format_vacancy_card(job) for job in jobs)
    await message.answer(
        f"<b>Опубликованные вакансии · {tenant_id}</b>\n\n{body}",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
