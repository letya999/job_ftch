"""Channel configuration handler — /channel command."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from job_ftch.adapters.telegram_bot.utils import (
    require_admin_callback,
    require_admin_message,
    safe_error_reply,
)

if TYPE_CHECKING:
    from aiogram.fsm.context import FSMContext

    from job_ftch.adapters.telegram_bot.config import TelegramBotConfig
    from job_ftch.application.tenant_runner import TenantRunner

logger = structlog.get_logger(__name__)
router = Router(name="channel")

_CHANNEL_RE = re.compile(r"^(@[a-zA-Z][a-zA-Z0-9_]{3,}|-100\d{8,})$")


class ChannelForm(StatesGroup):
    waiting_for_entity = State()


def _channel_keyboard(has_channel: bool) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="Изменить канал", callback_data="chan:change")],
    ]
    if has_channel:
        buttons.append(
            [InlineKeyboardButton(text="Отключить публикацию", callback_data="chan:off")]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("channel"))
async def cmd_channel(
    message: Message, runner: TenantRunner, state: FSMContext, config: TelegramBotConfig
) -> None:
    if not await require_admin_message(message, config):
        return
    try:
        user_id = str(message.from_user.id) if message.from_user else None
        tenant_id = await runner.get_selected_tenant_id(user_id)
        current = await runner.get_publish_channel(tenant_id)
        if current:
            text = (
                f"📢 Текущий канал публикации: <b>{current}</b>\n\n"
                "Вакансии из /run будут отправлены в этот канал.\n"
                "Бот должен быть администратором канала с правом публикации."
            )
        else:
            text = (
                "📢 Канал публикации не настроен.\n\n"
                "Вакансии из /run отправляются только в этот чат.\n"
                "Настрой канал, чтобы публиковать вакансии автоматически."
            )
        await message.answer(
            text,
            reply_markup=_channel_keyboard(has_channel=bool(current)),
            parse_mode="HTML",
        )
    except Exception as e:
        await safe_error_reply(message, e, "cmd_channel")


@router.callback_query(F.data == "chan:off")
async def callback_channel_off(
    query: CallbackQuery, runner: TenantRunner, state: FSMContext, config: TelegramBotConfig
) -> None:
    if not await require_admin_callback(query, config):
        return
    try:
        await query.answer()
        user_id = str(query.from_user.id) if query.from_user else None
        tenant_id = await runner.get_selected_tenant_id(user_id)
        await runner.set_publish_channel(tenant_id, None)
        if isinstance(query.message, Message):
            await query.message.edit_text("✅ Публикация в канал отключена.")
    except Exception as e:
        if isinstance(query.message, Message):
            await safe_error_reply(query.message, e, "callback_channel_off")


@router.callback_query(F.data == "chan:change")
async def callback_channel_change(
    query: CallbackQuery, state: FSMContext, config: TelegramBotConfig
) -> None:
    if not await require_admin_callback(query, config):
        return
    await query.answer()
    await state.set_state(ChannelForm.waiting_for_entity)
    if isinstance(query.message, Message):
        await query.message.edit_text(
            "Отправь username канала или его числовой ID:\n\n"
            "• Username: <code>@mychannel</code>\n"
            "• Числовой ID: <code>-1001234567890</code>\n\n"
            "Бот должен быть администратором канала с правом публикации.",
            parse_mode="HTML",
        )


@router.message(ChannelForm.waiting_for_entity, F.text)
async def handle_channel_entity(
    message: Message,
    state: FSMContext,
    runner: TenantRunner,
    config: TelegramBotConfig,
    bot: Bot | None = None,
) -> None:
    if not await require_admin_message(message, config):
        return
    text = (message.text or "").strip()
    if not _CHANNEL_RE.match(text):
        await message.answer("Неверный формат. Используй @username или числовой ID (-100...).")
        return
    try:
        if bot is not None:
            try:
                chat = await bot.get_chat(text)
                me = await bot.get_me()
                member = await bot.get_chat_member(chat.id, me.id)
            except TelegramForbiddenError:
                await message.answer(
                    "Бот не имеет доступа к этому каналу. Добавь его в канал и выдай право публикации."
                )
                return
            except TelegramBadRequest:
                await message.answer(
                    "Не удалось проверить канал. Проверь username/ID и что бот добавлен в канал."
                )
                return

            status = getattr(member, "status", None)
            can_post = getattr(member, "can_post_messages", None)
            if status not in {"administrator", "creator"}:
                await message.answer(
                    "Бот найден в канале, но не является администратором. Выдай ему права на публикацию."
                )
                return
            if status == "administrator" and can_post is False:
                await message.answer(
                    "Бот администратор, но без права публикации сообщений. Включи публикацию постов."
                )
                return
        user_id = str(message.from_user.id) if message.from_user else None
        tenant_id = await runner.get_selected_tenant_id(user_id)
        await runner.set_publish_channel(tenant_id, text, user_id=user_id)
        runtime = runner.get_runtime(tenant_id)
        await runtime.store.set_run_state(
            "bot_scheduler:last_attempt_at", datetime.now(UTC).isoformat()
        )
        await state.clear()
        await message.answer(
            f"✅ Канал <b>{text}</b> сохранён.\n\nСледующий /run опубликует вакансии в этот канал.",
            parse_mode="HTML",
        )
    except Exception as e:
        await safe_error_reply(message, e, "handle_channel_entity")
