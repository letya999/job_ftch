from typing import TYPE_CHECKING

import structlog
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

if TYPE_CHECKING:
    from job_ftch.adapters.telegram_bot.config import TelegramBotConfig
    from job_ftch.application.tenant_runner import TenantRunner

router = Router(name="base")
logger = structlog.get_logger(__name__)


class StartMenu(CallbackData, prefix="start"):
    action: str


class TenantMenu(CallbackData, prefix="tenant"):
    index: int


@router.message(Command("start"))
async def cmd_start(message: Message, runner: "TenantRunner") -> None:
    user_id_str = str(message.from_user.id) if message.from_user else "0"
    tenant_id = await runner.get_selected_tenant_id(user_id_str)
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
    problematic_sources = sum(
        1
        for source in sources
        if str(source.get("status", "pending")) in {"failing", "degraded", "paused"}
    )

    text = (
        f"Привет! Вот твой текущий статус:\n"
        f"Tenant: {tenant_id}\n"
        f"📗 Позитивных примеров: {pos}\n"
        f"📕 Негативных: {neg}\n"
        f"📡 Источников: {k_sources}"
        f"{f'  (проблемных: {problematic_sources})' if problematic_sources else ''}\n\n"
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
    builder.button(text="Tenant", callback_data=StartMenu(action="tenant"))
    builder.adjust(2, 1, 2)

    await message.answer(text, reply_markup=builder.as_markup())


@router.callback_query(StartMenu.filter(F.action == "add_positive"))
async def cb_add_pos(callback: CallbackQuery, runner: "TenantRunner", state: FSMContext) -> None:
    from job_ftch.adapters.telegram_bot.handlers.examples import cmd_positive

    msg = callback.message
    if isinstance(msg, Message):
        await cmd_positive(msg, state, runner, user_id_override=callback.from_user.id)
    await callback.answer()


@router.callback_query(StartMenu.filter(F.action == "add_negative"))
async def cb_add_neg(callback: CallbackQuery, runner: "TenantRunner", state: FSMContext) -> None:
    from job_ftch.adapters.telegram_bot.handlers.examples import cmd_negative

    msg = callback.message
    if isinstance(msg, Message):
        await cmd_negative(msg, state, runner, user_id_override=callback.from_user.id)
    await callback.answer()


@router.callback_query(StartMenu.filter(F.action == "show_examples"))
async def cb_show_ex(callback: CallbackQuery, runner: "TenantRunner") -> None:
    from job_ftch.adapters.telegram_bot.handlers.examples import cmd_examples

    msg = callback.message
    if isinstance(msg, Message):
        await cmd_examples(msg, runner, user_id_override=callback.from_user.id)
    await callback.answer()


@router.callback_query(StartMenu.filter(F.action == "show_sources"))
async def cb_show_src(
    callback: CallbackQuery, runner: "TenantRunner", config: "TelegramBotConfig"
) -> None:
    from job_ftch.adapters.telegram_bot.handlers.sources import cmd_sources

    msg = callback.message
    if isinstance(msg, Message):
        await cmd_sources(msg, runner, config, user_id_override=callback.from_user.id)
    await callback.answer()


@router.callback_query(StartMenu.filter(F.action == "run"))
async def cb_run(callback: CallbackQuery, runner: "TenantRunner", bot: Bot) -> None:
    from job_ftch.adapters.telegram_bot.handlers.pipeline import run_pipeline_for_chat

    msg = callback.message
    if isinstance(msg, Message):
        await run_pipeline_for_chat(msg, runner, bot, user_id_override=callback.from_user.id)
    await callback.answer()


@router.callback_query(StartMenu.filter(F.action == "tenant"))
async def cb_tenant_menu(callback: CallbackQuery, runner: "TenantRunner") -> None:
    msg = callback.message
    if isinstance(msg, Message):
        await cmd_tenant(msg, runner, user_id_override=callback.from_user.id)
    await callback.answer()


@router.message(Command("tenant"))
async def cmd_tenant(
    message: Message, runner: "TenantRunner", user_id_override: int | None = None
) -> None:
    user_id = str(
        user_id_override
        if user_id_override is not None
        else (message.from_user.id if message.from_user else 0)
    )
    current = await runner.get_selected_tenant_id(user_id)
    tenant_ids = runner.tenant_ids()
    builder = InlineKeyboardBuilder()
    for index, tenant_id in enumerate(tenant_ids):
        marker = "✓ " if tenant_id == current else ""
        builder.button(text=f"{marker}{tenant_id}", callback_data=TenantMenu(index=index))
    builder.adjust(1)
    await message.answer(
        f"Текущий tenant: {current}\nВыбери tenant для своих команд:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(TenantMenu.filter())
async def cb_set_tenant(
    callback: CallbackQuery, callback_data: TenantMenu, runner: "TenantRunner"
) -> None:
    tenant_ids = runner.tenant_ids()
    if callback_data.index < 0 or callback_data.index >= len(tenant_ids):
        await callback.answer("Tenant не найден.", show_alert=True)
        return
    tenant_id = tenant_ids[callback_data.index]
    await runner.set_selected_tenant_id(str(callback.from_user.id), tenant_id)
    await callback.answer(f"Tenant выбран: {tenant_id}")
    if isinstance(callback.message, Message):
        await callback.message.edit_text(f"Текущий tenant: {tenant_id}")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "Используй кнопки в /start или команды:\n"
        "/positive — добавить подходящее резюме\n"
        "/negative — добавить НЕ подходящее резюме\n"
        "/positive_job — добавить подходящую вакансию\n"
        "/negative_job — добавить НЕ подходящую вакансию\n"
        "/examples — посмотреть свои примеры\n"
        "/resumes — только резюме\n"
        "/vacancies — только вакансии\n"
        "/tenant — выбрать tenant\n"
        "/sources — настроить источники (URL)\n"
        "/run — запустить поиск вакансий\n"
        "/clear — очистить историю, следующий запуск увидит всё заново\n"
        "/schedule — частота автозапуска\n"
        "/channel — канал публикации\n"
        "/feedback — обратная связь на опубликованные вакансии\n"
        "/status — состояние последнего запуска\n"
        "/cancel — отменить текущее действие"
    )
    await message.answer(text)


@router.message(Command("status"))
async def cmd_status(message: Message, runner: "TenantRunner") -> None:
    user_id = str(message.from_user.id) if message.from_user else "0"
    tenant_id = await runner.get_selected_tenant_id(user_id)
    summary = await runner.get_status(tenant_id)
    if summary is None:
        await message.answer(f"Tenant: {tenant_id}\nЗапусков еще не было.")
    else:
        await message.answer(
            f"Tenant: {tenant_id}\nПоследний запуск:\n"
            f"✅ Найдено: {summary.emitted}\n"
            f"❌ Ошибок: {summary.failed}\n"
            f"⚠️ В карантине: {summary.quarantined}"
        )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is None:
        await message.answer("Нечего отменять.")
        return
    await state.clear()
    await message.answer("❌ Отменено. /start — главное меню")
