"""Document upload handlers for the Telegram bot."""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from adapters.telegram_bot.fsm.states import UploadMode
from job_ftch.application.profile_inputs import (
    build_profile_from_resume_text_async,
    embed_profile_examples,
    merge_resume_profile,
)
from job_ftch.infrastructure.document_parser import parse_document

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.fsm.context import FSMContext

    from job_ftch.application.tenant_runner import TenantRunner

logger = structlog.get_logger(__name__)

router = Router(name="upload")


class ModeCallback(CallbackData, prefix="mode"):
    mode: str


def build_mode_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(
            text="Positive Resume",
            callback_data=ModeCallback(mode=UploadMode.POSITIVE_RESUME).pack(),
        )
    )
    builder.add(
        InlineKeyboardButton(
            text="Negative Resume",
            callback_data=ModeCallback(mode=UploadMode.NEGATIVE_RESUME).pack(),
        )
    )
    builder.adjust(2)
    return builder


def _profile_id(user_id: str) -> str:
    return f"user_{user_id}"


def _profile_summary(managed_profile) -> str:  # type: ignore[no-untyped-def]
    """Return a short human-readable summary of profile state."""
    if not managed_profile.profile.search_profiles:
        return ""
    sp = managed_profile.profile.search_profiles[0]
    pos = len(sp.positive_example_texts)
    neg = len(sp.negative_example_texts)
    roles = list(sp.target_roles)[:3]
    skills = [s.canonical_name for s in sp.required_skills][:5]
    lines = [f"Positive examples: {pos}  |  Negative: {neg}"]
    if roles:
        lines.append(f"Roles: {', '.join(roles)}")
    if skills:
        lines.append(f"Skills: {', '.join(skills)}")
    return "\n".join(lines)


@router.message(Command("mode"))
async def cmd_mode(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    current_mode = data.get("upload_mode", UploadMode.POSITIVE_RESUME)
    await message.answer(
        f"Choose upload mode (current: {current_mode.replace('_', ' ')}):",
        reply_markup=build_mode_keyboard().as_markup(),
    )


@router.callback_query(ModeCallback.filter())
async def set_mode(callback: CallbackQuery, callback_data: ModeCallback, state: FSMContext) -> None:
    if not isinstance(callback.message, Message):
        return
    await state.update_data(upload_mode=callback_data.mode)
    await callback.answer(f"Mode set to {callback_data.mode.replace('_', ' ')}")
    await callback.message.edit_text(f"Upload mode set to: {callback_data.mode.replace('_', ' ')}")


@router.message(F.document, F.chat.type == "private")
async def handle_document(
    message: Message,
    bot: Bot,
    runner: TenantRunner,
    state: FSMContext,
) -> None:
    document = message.document
    if not document:
        return

    data = await state.get_data()
    mode = data.get("upload_mode", UploadMode.POSITIVE_RESUME)
    user_id_str = str(message.from_user.id if message.from_user else 0)
    profile_id = _profile_id(user_id_str)

    status_msg = await message.answer(f"Processing your {mode.replace('_', ' ')}...")

    try:
        file = await bot.get_file(document.file_id)
        if not file.file_path:
            await status_msg.edit_text("Error: Could not get file path.")
            return

        content_io = BytesIO()
        await bot.download_file(file.file_path, content_io)
        content = content_io.getvalue()

        text = parse_document(content, document.file_name or "resume.txt")
        if not text.strip():
            await status_msg.edit_text("Could not extract text from the file.")
            return

        tenant_ids = runner.tenant_ids()
        tenant_id = tenant_ids[0] if tenant_ids else "default"
        runtime = runner.get_runtime(tenant_id)
        llm_provider = runtime.llm_provider
        embedding_provider = runtime.embedding_provider

        is_negative = mode == UploadMode.NEGATIVE_RESUME

        # LLM-extract the uploaded PDF
        extracted = await build_profile_from_resume_text_async(
            text,
            user_id=user_id_str,
            profile_id=profile_id,
            llm_provider=llm_provider,
        )

        # Load existing single-user profile (or start fresh)
        existing = await runner.get_candidate_profile(tenant_id, user_id_str, profile_id)

        if existing is not None:
            managed = merge_resume_profile(existing, extracted, is_negative=is_negative)
        else:
            # First upload: merge with itself to seed positive_example_texts / negative_example_texts
            managed = merge_resume_profile(extracted, extracted, is_negative=is_negative)

        if embedding_provider:
            managed = await embed_profile_examples(managed, embedding_provider)

        await runner.save_candidate_profile(tenant_id, managed)
        await runner.set_active_candidate_profile(tenant_id, user_id_str, profile_id)

        action = "Negative example added" if is_negative else "Positive resume added"
        summary = _profile_summary(managed)
        await status_msg.edit_text(f"{action}.\n\n{summary}")

    except Exception as exc:
        logger.error("upload_failed", mode=mode, error=str(exc), exc_info=True)
        await status_msg.edit_text(f"Error processing upload: {exc}")
