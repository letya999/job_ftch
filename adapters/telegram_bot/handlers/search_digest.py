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


@router.message(Command("digest"))
async def cmd_digest(
    message: Message,
    runner: TenantRunner,
    config: TelegramBotConfig,
    reranker: CrossEncoderPort | None = None,
) -> None:
    """Handle /digest command."""
    if message.text is None:
        return
    args = message.text.split()[1:]
    tenant_ids = runner.tenant_ids()
    digest_tenant_id = args[0] if args else (tenant_ids[0] if tenant_ids else "default")
    user_id_str = str(message.from_user.id if message.from_user else 0)

    jobs = await runner.latest_jobs(
        digest_tenant_id,
        limit=config.digest_size * 5,  # fetch more for reranking
        user_id=user_id_str,
    )

    if reranker and len(jobs) > 1:
        try:
            # Simple heuristic: use a generic query for now
            # TODO: get specific interests from profile if available
            profile_query = "software engineer developer"
            docs = [f"{j.title} {(j.description or '')[:200]}" for j in jobs]
            scores = await reranker.rerank(profile_query, docs)
            jobs = [
                j
                for j, _ in sorted(
                    zip(jobs, scores, strict=False), key=lambda x: x[1], reverse=True
                )
            ]
        except Exception:
            pass  # Fallback to default order

    # Format first page
    text = format_job_digest(jobs, page=0, page_size=config.digest_size)
    total_pages = (len(jobs) + config.digest_size - 1) // config.digest_size

    kb = build_digest_kb(tenant_id=digest_tenant_id, page=0, total_pages=total_pages)
    await message.answer(text, reply_markup=kb)


@router.callback_query(DigestPage.filter())
async def handle_digest_page(
    callback: CallbackQuery,
    callback_data: DigestPage,
    runner: TenantRunner,
    config: TelegramBotConfig,
) -> None:
    """Handle digest pagination."""
    if not isinstance(callback.message, Message):
        return
    jobs = await runner.latest_jobs(
        callback_data.tenant_id,
        limit=(callback_data.page + 1) * config.digest_size,
    )

    text = format_job_digest(jobs, page=callback_data.page, page_size=config.digest_size)
    # We don't know total jobs easily without fetching all, but we can guess
    # For simplicity, always show "Next" if we got a full page
    total_pages = (
        callback_data.page + 2
        if len(jobs) >= (callback_data.page + 1) * config.digest_size
        else callback_data.page + 1
    )

    kb = build_digest_kb(
        tenant_id=callback_data.tenant_id,
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
