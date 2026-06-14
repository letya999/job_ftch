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
    add_example_to_profile,
    build_profile_from_resume_text,
    embed_profile_examples,
)
from job_ftch.infrastructure.document_parser import parse_document

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.fsm.context import FSMContext

    from job_ftch.application.contracts import EmbeddingProvider
    from job_ftch.application.tenant_runner import TenantRunner

logger = structlog.get_logger(__name__)

router = Router(name="upload")


class ModeCallback(CallbackData, prefix="mode"):
    """Callback data for mode selection."""

    mode: str


def build_mode_keyboard() -> InlineKeyboardBuilder:
    """Build keyboard for mode selection."""
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(
            text="Positive Resume", callback_data=ModeCallback(mode=UploadMode.POSITIVE_RESUME).pack()
        )
    )
    builder.add(
        InlineKeyboardButton(
            text="Negative Resume", callback_data=ModeCallback(mode=UploadMode.NEGATIVE_RESUME).pack()
        )
    )
    builder.add(
        InlineKeyboardButton(
            text="Positive Job", callback_data=ModeCallback(mode=UploadMode.POSITIVE_JOB).pack()
        )
    )
    builder.add(
        InlineKeyboardButton(
            text="Negative Job", callback_data=ModeCallback(mode=UploadMode.NEGATIVE_JOB).pack()
        )
    )
    builder.adjust(2)
    return builder


@router.message(Command("mode"))
async def cmd_mode(message: Message, state: FSMContext) -> None:
    """Handle /mode command."""
    data = await state.get_data()
    current_mode = data.get("upload_mode", UploadMode.POSITIVE_RESUME)
    await message.answer(
        f"Choose upload mode (current: {current_mode.replace('_', ' ')}):",
        reply_markup=build_mode_keyboard().as_markup(),
    )


@router.callback_query(ModeCallback.filter())
async def set_mode(callback: CallbackQuery, callback_data: ModeCallback, state: FSMContext) -> None:
    """Handle mode selection callback."""
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
    embedding_provider: EmbeddingProvider | None = None,
) -> None:
    """Handle document upload."""
    document = message.document
    if not document:
        return

    data = await state.get_data()
    mode = data.get("upload_mode", UploadMode.POSITIVE_RESUME)
    user_id_str = str(message.from_user.id if message.from_user else 0)

    status_msg = await message.answer(f"Processing your {mode.replace('_', ' ')}...")

    try:
        # Download file
        file = await bot.get_file(document.file_id)
        if not file.file_path:
            await status_msg.edit_text("Error: Could not get file path.")
            return

        # aiogram 3.x way to download file
        content_io = BytesIO()
        await bot.download_file(file.file_path, content_io)
        content = content_io.getvalue()

        text = parse_document(content, document.file_name or "resume.txt")
        if not text.strip():
            await status_msg.edit_text("Could not extract text from the file.")
            return

        # Default to first tenant if multiple
        tenant_ids = runner.tenant_ids()
        tenant_id = tenant_ids[0] if tenant_ids else "default"

        if mode == UploadMode.POSITIVE_RESUME:
            managed_profile = build_profile_from_resume_text(text, user_id=user_id_str)
            if embedding_provider:
                managed_profile = await embed_profile_examples(
                    managed_profile, embedding_provider
                )
            await runner.save_candidate_profile(tenant_id, managed_profile)
            await runner.set_active_candidate_profile(
                tenant_id, user_id_str, managed_profile.profile_id
            )
            await status_msg.edit_text(
                f"Profile created from your resume. Active: {managed_profile.profile_id}",
            )
        elif mode == UploadMode.NEGATIVE_RESUME:
            profiles = await runner.list_candidate_profiles(tenant_id, user_id_str)
            active_profile_payload = next((p for p in profiles if p["active"]), None)

            if active_profile_payload:
                existing_profile = await runner.get_candidate_profile(
                    tenant_id, user_id_str, active_profile_payload["profile_id"]
                )
                if existing_profile:
                    updated_profile = add_example_to_profile(
                        existing_profile, text, kind="negative_resume"
                    )
                    if embedding_provider:
                        updated_profile = await embed_profile_examples(
                            updated_profile, embedding_provider
                        )
                    await runner.save_candidate_profile(tenant_id, updated_profile)
                    await status_msg.edit_text("Negative resume example added to your active profile.")
                else:
                    await status_msg.edit_text("Error: Could not load your active profile.")
            else:
                managed_profile = build_profile_from_resume_text(text, user_id=user_id_str)
                updated_profile = add_example_to_profile(
                    managed_profile, text, kind="negative_resume"
                )
                await runner.save_candidate_profile(tenant_id, updated_profile)
                await runner.set_active_candidate_profile(
                    tenant_id, user_id_str, updated_profile.profile_id
                )
                await status_msg.edit_text(
                    f"New profile created with negative resume example. Active: {updated_profile.profile_id}",
                )
        elif mode in (UploadMode.POSITIVE_JOB, UploadMode.NEGATIVE_JOB):
            profiles = await runner.list_candidate_profiles(tenant_id, user_id_str)
            active_profile_payload = next((p for p in profiles if p["active"]), None)

            if not active_profile_payload:
                await status_msg.edit_text("Error: You must have an active profile to add job examples.")
                return

            active_profile = await runner.get_candidate_profile(
                tenant_id, user_id_str, active_profile_payload["profile_id"]
            )
            if active_profile:
                updated_profile = add_example_to_profile(active_profile, text, kind=mode)
                if embedding_provider:
                    updated_profile = await embed_profile_examples(
                        updated_profile, embedding_provider
                    )
                await runner.save_candidate_profile(tenant_id, updated_profile)
                await status_msg.edit_text(f"Job example added as {mode.replace('_', ' ')} to your profile.")
            else:
                await status_msg.edit_text("Error: Could not load your active profile.")

    except Exception as exc:
        logger.error("upload_failed", mode=mode, error=str(exc), exc_info=True)
        await status_msg.edit_text(f"Error processing upload: {exc}")
