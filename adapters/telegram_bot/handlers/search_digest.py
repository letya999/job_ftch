"""Search and digest handlers for the Telegram bot."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from adapters.telegram_bot.formatter import format_job_digest, format_job_message
from adapters.telegram_bot.keyboards.pagination import (
    DigestPage,
    SearchPage,
    build_digest_kb,
)

if TYPE_CHECKING:
    from aiogram.fsm.context import FSMContext

    from adapters.telegram_bot.config import TelegramBotConfig
    from job_ftch.application.contracts import CrossEncoderPort
    from job_ftch.application.tenant_runner import TenantRunner

router = Router(name="search_digest")


import time

@router.message(Command("digest"))
async def cmd_digest(
    message: Message,
    runner: TenantRunner,
    config: TelegramBotConfig,
    state: FSMContext,
) -> None:
    """Handle /digest command."""
    if message.text is None:
        return
    args = message.text.split()[1:]
    tenant_ids = runner.tenant_ids()
    digest_tenant_id = args[0] if args else (tenant_ids[0] if tenant_ids else "default")
    user_id_str = str(message.from_user.id if message.from_user else 0)

    # Fetch a larger pool to rerank via active profile natively
    pool_size = config.digest_size * 10
    jobs = await runner.latest_jobs(
        digest_tenant_id,
        limit=pool_size,
        user_id=user_id_str,
    )

    if not jobs:
        await message.answer("No jobs available.")
        return

    # Store ordered job IDs in FSM
    digest_hash = hashlib.md5(f"digest_{user_id_str}_{time.time()}".encode()).hexdigest()[:8]
    job_ids = [j.item_id for j in jobs]
    await state.update_data(
        {f"digest_jobs_{digest_hash}": job_ids, f"tenant_{digest_hash}": digest_tenant_id}
    )

    # Format first page
    page_jobs = jobs[:config.digest_size]
    text = format_job_digest(page_jobs, page=0, page_size=config.digest_size)
    total_pages = (len(jobs) + config.digest_size - 1) // config.digest_size

    kb = build_digest_kb(digest_hash=digest_hash, page=0, total_pages=total_pages)
    await message.answer(text, reply_markup=kb)


@router.callback_query(DigestPage.filter())
async def handle_digest_page(
    callback: CallbackQuery,
    callback_data: DigestPage,
    runner: TenantRunner,
    config: TelegramBotConfig,
    state: FSMContext,
) -> None:
    """Handle digest pagination."""
    if not isinstance(callback.message, Message):
        return
        
    data = await state.get_data()
    job_ids = data.get(f"digest_jobs_{callback_data.digest_hash}")
    tenant_id = data.get(f"tenant_{callback_data.digest_hash}")

    if not job_ids:
        await callback.answer("Digest session expired. Please run /digest again.", show_alert=True)
        return

    start_idx = callback_data.page * config.digest_size
    end_idx = start_idx + config.digest_size
    page_ids = job_ids[start_idx:end_idx]

    jobs = []
    for jid in page_ids:
        job = await runner.get_job(jid, tenant_id=tenant_id)
        if job:
            jobs.append(job)

    if not jobs:
        await callback.answer("No more jobs.", show_alert=True)
        return

    text = format_job_digest(jobs, page=callback_data.page, page_size=config.digest_size)
    total_pages = (len(job_ids) + config.digest_size - 1) // config.digest_size

    kb = build_digest_kb(
        digest_hash=callback_data.digest_hash,
        page=callback_data.page,
        total_pages=total_pages,
    )
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.message(Command("search"))
async def cmd_search(message: Message, runner: TenantRunner, state: FSMContext) -> None:
    """Handle /search command."""
    if message.text is None:
        return
    args = message.text.split()[1:]
    tenant_ids = runner.tenant_ids()

    search_tenant_id: str | None = None
    if args and args[-1] in tenant_ids:
        search_tenant_id = args[-1]
        args = args[:-1]

    query = " ".join(args)
    if not query:
        await message.answer("Usage: /search <query> [tenant_id]")
        return

    user_id_str = str(message.from_user.id if message.from_user else 0)
    results = await runner.search_jobs(
        query,
        tenant_id=search_tenant_id,
        user_id=user_id_str,
        limit=10,
    )

    if not results:
        await message.answer("No matches.")
        return

    # Store query in FSM to avoid long callback data
    query_hash = hashlib.md5(query.encode()).hexdigest()[:8]
    await state.update_data(
        {f"query_{query_hash}": query, f"tenant_{query_hash}": search_tenant_id}
    )

    group = results[0]
    text = format_job_message(group.canonical_job)

    # Inline keyboard with URL and Next button
    builder = InlineKeyboardBuilder()
    if group.canonical_job.canonical_url:
        builder.add(
            InlineKeyboardButton(text="Open URL", url=str(group.canonical_job.canonical_url))
        )

    if len(results) > 1:
        builder.add(
            InlineKeyboardButton(
                text="Next ➡️",
                callback_data=SearchPage(query_hash=query_hash, page=1).pack(),
            )
        )

    await message.answer(text, reply_markup=builder.as_markup())


@router.callback_query(SearchPage.filter())
async def handle_search_page(
    callback: CallbackQuery,
    callback_data: SearchPage,
    runner: TenantRunner,
    state: FSMContext,
) -> None:
    """Handle search pagination."""
    if not isinstance(callback.message, Message):
        return
    data = await state.get_data()
    query = data.get(f"query_{callback_data.query_hash}")
    tenant_id = data.get(f"tenant_{callback_data.query_hash}")

    if not query:
        await callback.answer("Search session expired. Please search again.", show_alert=True)
        return

    user_id_str = str(callback.from_user.id)
    results = await runner.search_jobs(
        query,
        tenant_id=tenant_id,
        user_id=user_id_str,
        limit=callback_data.page + 1,
    )

    if callback_data.page >= len(results):
        await callback.answer("No more results.")
        return

    group = results[callback_data.page]
    text = format_job_message(group.canonical_job)

    builder = InlineKeyboardBuilder()
    if group.canonical_job.canonical_url:
        builder.add(
            InlineKeyboardButton(text="Open URL", url=str(group.canonical_job.canonical_url))
        )

    if callback_data.page > 0:
        builder.add(
            InlineKeyboardButton(
                text="⬅️ Previous",
                callback_data=SearchPage(
                    query_hash=callback_data.query_hash, page=callback_data.page - 1
                ).pack(),
            )
        )

    if callback_data.page < len(results) - 1:
        builder.add(
            InlineKeyboardButton(
                text="Next ➡️",
                callback_data=SearchPage(
                    query_hash=callback_data.query_hash, page=callback_data.page + 1
                ).pack(),
            )
        )

    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()
