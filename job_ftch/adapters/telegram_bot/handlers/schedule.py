"""Schedule configuration handler — /schedule command."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import structlog
from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from job_ftch.adapters.telegram_bot.fsm.states import SettingSchedule
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
router = Router(name="schedule")

_INTERVALS = [
    ("1 час", 3600),
    ("4 часа", 14400),
    ("12 часов", 43200),
    ("24 часа", 86400),
]
_INTERVAL_RE = re.compile(r"^\s*(\d+)\s*([smhd]?)\s*$", re.IGNORECASE)


def _schedule_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=label, callback_data=f"sched:{seconds}")]
        for label, seconds in _INTERVALS
    ]
    buttons.append([InlineKeyboardButton(text="Свое значение", callback_data="sched:custom")])
    buttons.append([InlineKeyboardButton(text="Отключить автозапуск", callback_data="sched:off")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _format_interval(seconds: int | None) -> str:
    if seconds is None:
        return "не настроен (используется значение из конфига)"
    hours = seconds // 3600
    if hours == 1:
        return "каждый час"
    if hours < 24:
        return f"каждые {hours} часа/часов"
    return f"каждые {seconds // 86400} дня/дней"


def _parse_interval_seconds(text: str) -> int:
    match = _INTERVAL_RE.fullmatch(text)
    if match is None:
        msg = "interval must be an integer optionally suffixed with s/m/h/d"
        raise ValueError(msg)
    value = int(match.group(1))
    unit = match.group(2).lower()
    multiplier = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    seconds = value * multiplier
    if seconds <= 0:
        msg = "interval must be > 0"
        raise ValueError(msg)
    return seconds


def _format_timestamp(value: str | None) -> str:
    if not value:
        return "нет"
    return value.replace("T", " ")


@router.message(Command("schedule"))
async def cmd_schedule(message: Message, runner: TenantRunner, config: TelegramBotConfig) -> None:
    if not await require_admin_message(message, config):
        return
    try:
        user_id = str(message.from_user.id) if message.from_user else None
        tenant_id = await runner.get_selected_tenant_id(user_id)
        current = await runner.get_schedule_interval(tenant_id)
        scheduler_status = await runner.get_bot_scheduler_status(tenant_id)
        status_lines = [
            f"Последняя попытка: {_format_timestamp(scheduler_status.get('last_attempt_at'))}",
            f"Последний успешный run: {_format_timestamp(scheduler_status.get('last_success_at'))}",
        ]
        if scheduler_status.get("last_run_emitted"):
            status_lines.append(
                f"Найдено в последнем auto-run: {scheduler_status['last_run_emitted']}"
            )
        if scheduler_status.get("last_publish_sent"):
            status_lines.append(f"Опубликовано в канал: {scheduler_status['last_publish_sent']}")
        if scheduler_status.get("last_publish_success_at"):
            status_lines.append(
                "Последняя успешная публикация: "
                f"{_format_timestamp(scheduler_status['last_publish_success_at'])}"
            )
        if scheduler_status.get("last_error"):
            status_lines.append(f"Ошибка run: {scheduler_status['last_error']}")
        if scheduler_status.get("last_publish_error"):
            status_lines.append(f"Ошибка публикации: {scheduler_status['last_publish_error']}")
        text = (
            f"⏱ Текущий интервал ингеста: <b>{_format_interval(current)}</b>\n\n"
            + "\n".join(status_lines)
            + "\n\n"
            "Выбери новый интервал или оставь как есть:"
        )
        await message.answer(text, reply_markup=_schedule_keyboard(), parse_mode="HTML")
    except Exception as e:
        await safe_error_reply(message, e, "cmd_schedule")


@router.callback_query(F.data.startswith("sched:"))
async def callback_set_schedule(
    query: CallbackQuery,
    runner: TenantRunner,
    config: TelegramBotConfig,
    state: FSMContext,
) -> None:
    if not await require_admin_callback(query, config):
        return
    try:
        await query.answer()
        if not query.data:
            return
        raw = query.data.split(":", 1)[1]
        user_id = str(query.from_user.id) if query.from_user else None
        tenant_id = await runner.get_selected_tenant_id(user_id)

        if raw == "custom":
            await state.set_state(SettingSchedule.waiting_for_interval)
            text = "Отправь интервал одним сообщением: 900, 15m, 2h или 1d.\nМинимум 1 секунда."
        elif raw == "off":
            await runner.set_schedule_interval(tenant_id, None)
            text = "✅ Автозапуск отключён."
        else:
            seconds = int(raw)
            await runner.set_schedule_interval(tenant_id, seconds)
            text = f"✅ Интервал установлен: <b>{_format_interval(seconds)}</b>\n\nБот будет автоматически запускать поиск и публиковать новые вакансии в настроенный канал (/channel)."

        if query.message:
            if isinstance(query.message, Message):
                await query.message.edit_text(text, parse_mode="HTML")
            else:
                pass
    except Exception as e:
        if query.message and isinstance(query.message, Message):
            await safe_error_reply(query.message, e, "callback_set_schedule")


@router.message(StateFilter(SettingSchedule.waiting_for_interval), F.text)
async def handle_custom_schedule_interval(
    message: Message,
    state: FSMContext,
    runner: TenantRunner,
    config: TelegramBotConfig,
) -> None:
    if not await require_admin_message(message, config):
        return
    try:
        seconds = _parse_interval_seconds(message.text or "")
        user_id = str(message.from_user.id) if message.from_user else None
        tenant_id = await runner.get_selected_tenant_id(user_id)
        await runner.set_schedule_interval(tenant_id, seconds)
        await state.clear()
        await message.answer(
            f"✅ Интервал установлен: <b>{_format_interval(seconds)}</b>",
            parse_mode="HTML",
        )
    except Exception as exc:
        await safe_error_reply(message, exc, "handle_custom_schedule_interval")
