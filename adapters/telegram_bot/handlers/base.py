from typing import TYPE_CHECKING

import structlog
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

if TYPE_CHECKING:
    from job_ftch.application.tenant_runner import TenantRunner

router = Router(name="base")
logger = structlog.get_logger(__name__)


class StartMenu(CallbackData, prefix="start"):
    action: str


@router.message(Command("start"))
async def cmd_start(message: Message, runner: "TenantRunner") -> None:
    tenant_ids = runner.tenant_ids()
    tenant_id = tenant_ids[0] if tenant_ids else "default"
    user_id_str = str(message.from_user.id) if message.from_user else "0"
    profile_id = f"user_{user_id_str}"

    # Get current status
    profile = await runner.get_candidate_profile(tenant_id, user_id_str, profile_id)
    pos, neg = 0, 0
    if profile and profile.profile.search_profiles:
        sp = profile.profile.search_profiles[0]
        pos = len(sp.positive_example_texts)
        neg = len(sp.negative_example_texts)

    sources = await runner.list_sources(tenant_id)
    k_sources = len(sources)

    text = (
        f"Привет! Вот твой текущий статус:\n"
        f"📗 Позитивных примеров: {pos}\n"
        f"📕 Негативных: {neg}\n"
        f"📡 Источников: {k_sources}\n\n"
        f"Выбери действие:"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="📗 Добавить (+)", callback_data=StartMenu(action="add_positive"))
    builder.button(text="📕 Добавить (−)", callback_data=StartMenu(action="add_negative"))
    builder.button(
        text=f"📋 Примеры ({pos}/{neg})", callback_data=StartMenu(action="show_examples")
    )
    builder.button(
        text=f"🔗 Источники ({k_sources})", callback_data=StartMenu(action="show_sources")
    )
    builder.button(text="▶ Запустить поиск", callback_data=StartMenu(action="run"))
    builder.adjust(2, 1, 2)

    await message.answer(text, reply_markup=builder.as_markup())


@router.callback_query(StartMenu.filter(F.action == "add_positive"))
async def cb_add_pos(callback: CallbackQuery, runner: "TenantRunner", state: FSMContext) -> None:
    from adapters.telegram_bot.handlers.examples import cmd_positive

    msg = callback.message
    if isinstance(msg, Message):
        await cmd_positive(msg, state, runner, user_id_override=callback.from_user.id)
    await callback.answer()


@router.callback_query(StartMenu.filter(F.action == "add_negative"))
async def cb_add_neg(callback: CallbackQuery, runner: "TenantRunner", state: FSMContext) -> None:
    from adapters.telegram_bot.handlers.examples import cmd_negative

    msg = callback.message
    if isinstance(msg, Message):
        await cmd_negative(msg, state, runner, user_id_override=callback.from_user.id)
    await callback.answer()


@router.callback_query(StartMenu.filter(F.action == "show_examples"))
async def cb_show_ex(callback: CallbackQuery, runner: "TenantRunner") -> None:
    from adapters.telegram_bot.handlers.examples import cmd_examples

    msg = callback.message
    if isinstance(msg, Message):
        await cmd_examples(msg, runner, user_id_override=callback.from_user.id)
    await callback.answer()


@router.callback_query(StartMenu.filter(F.action == "show_sources"))
async def cb_show_src(callback: CallbackQuery, runner: "TenantRunner") -> None:
    from adapters.telegram_bot.handlers.sources import cmd_sources

    msg = callback.message
    if isinstance(msg, Message):
        await cmd_sources(msg, runner, user_id_override=callback.from_user.id)
    await callback.answer()


@router.callback_query(StartMenu.filter(F.action == "run"))
async def cb_run(callback: CallbackQuery, runner: "TenantRunner", bot: Bot) -> None:
    from adapters.telegram_bot.handlers.pipeline import run_pipeline_for_chat

    msg = callback.message
    if isinstance(msg, Message):
        await run_pipeline_for_chat(msg, runner, bot, user_id_override=callback.from_user.id)
    await callback.answer()


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "Используй кнопки в /start или команды:\n"
        "/positive — добавить подходящее резюме\n"
        "/negative — добавить НЕ подходящее резюме\n"
        "/examples — посмотреть свои примеры\n"
        "/sources — настроить источники (URL)\n"
        "/run — запустить поиск вакансий"
    )
    await message.answer(text)


@router.message(Command("status"))
async def cmd_status(message: Message, runner: "TenantRunner") -> None:
    tenant_id = runner.default_tenant_id()
    summary = await runner.get_status(tenant_id)
    if summary is None:
        await message.answer("Запусков еще не было.")
    else:
        await message.answer(
            f"Последний запуск:\n"
            f"✅ Найдено: {summary.emitted}\n"
            f"❌ Ошибок: {summary.failed}\n"
            f"⚠️ В карантине: {summary.quarantined}"
        )


def create_router() -> Router:
    r = Router(name="base")
    r.message.register(cmd_start, Command("start"))
    r.callback_query.register(cb_add_pos, StartMenu.filter(F.action == "add_positive"))
    r.callback_query.register(cb_add_neg, StartMenu.filter(F.action == "add_negative"))
    r.callback_query.register(cb_show_ex, StartMenu.filter(F.action == "show_examples"))
    r.callback_query.register(cb_show_src, StartMenu.filter(F.action == "show_sources"))
    r.callback_query.register(cb_run, StartMenu.filter(F.action == "run"))
    r.message.register(cmd_help, Command("help"))
    r.message.register(cmd_status, Command("status"))
    return r
