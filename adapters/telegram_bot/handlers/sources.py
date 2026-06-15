import re
from typing import TYPE_CHECKING, Any

import structlog
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from adapters.telegram_bot.fsm.states import AddingSources
from job_ftch.application.source_inputs import build_source_spec_from_input

if TYPE_CHECKING:
    from job_ftch.application.tenant_runner import TenantRunner

logger = structlog.get_logger(__name__)
router = Router(name="sources")

class SourceAction(CallbackData, prefix="src"):
    action: str

@router.message(Command("sources"))
async def cmd_sources(message: Message, runner: "TenantRunner", user_id_override: int | None = None) -> None:
    resolved_uid = user_id_override if user_id_override is not None else (message.from_user.id if message.from_user else None)
    tenant_id = runner.default_tenant_id()
    payloads = await runner.list_sources(tenant_id)
    
    if not payloads:
        builder = InlineKeyboardBuilder()
        builder.button(text="➕ Добавить источники", callback_data=SourceAction(action="add"))
        await message.answer(
            "Источников нет. Нажми + чтобы добавить.",
            reply_markup=builder.as_markup()
        )
        return

    # Group by source_kind
    grouped: dict[str, list[dict[str, Any]]] = {}
    for p in payloads:
        kind = str(p.get("source_kind", "other"))
        grouped.setdefault(kind, []).append(p)
    
    lines = []
    kind_labels = {
        "telegram_channel": "📡 Telegram каналы",
        "telegram_group": "👥 Telegram группы",
        "web": "🌐 Web",
        "career_site": "🌐 Web (career site)",
        "api": "🔌 API",
        "rss": "📰 RSS",
        "other": "📦 Другое"
    }
    
    for kind, sources in grouped.items():
        label = kind_labels.get(kind, kind.title())
        lines.append(f"{label} ({len(sources)}):")
        for s in sources:
            # Show FULL URLs (origin field or source_name if it looks like URL)
            url = s.get("locator") or s.get("source_name") or s.get("source_id", "???")
            
            # Format URL based on source kind
            kind = str(s.get("source_kind", ""))
            if url and not url.startswith("http") and kind in ("telegram_channel", "telegram_group"):
                url = f"https://t.me/{url.lstrip('@')}"
                
            lines.append(f"• {url}")
        lines.append("")

    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить источники", callback_data=SourceAction(action="add"))
    builder.button(text="▶ Запустить пайплайн", callback_data=SourceAction(action="run"))
    builder.adjust(1)
    
    await message.answer(
        "\n".join(lines),
        reply_markup=builder.as_markup(),
        disable_web_page_preview=True
    )

@router.callback_query(SourceAction.filter(F.action == "add"))
async def callback_add_sources(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddingSources.waiting)
    msg = callback.message
    if isinstance(msg, Message):
        await msg.answer(
            "Кидай URL источников — каждый с новой строки или через пробел.\n\n"
            "Примеры:\n"
            "https://t.me/ai_jobs\n"
            "https://t.me/python_jobs\n"
            "https://hh.ru/search/vacancy?text=python"
        )
    await callback.answer()

@router.message(AddingSources.waiting, F.text, ~F.text.startswith("/"))
async def handle_sources_text(message: Message, state: FSMContext, runner: "TenantRunner") -> None:
    text = message.text or ""
    parts = re.split(r'[\s\n]+', text)
    urls = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if p.startswith("http") or p.startswith("t.me/") or p.startswith("@"):
            urls.append(p)

    if not urls:
        await message.answer("Не нашёл URL. Попробуй ещё раз или /cancel")
        return

    tenant_id = runner.default_tenant_id()
    ok, fail = 0, 0
    
    status_msg = await message.answer(f"⏳ Добавляю {len(urls)} источников...")
    
    runtime = runner.get_runtime(tenant_id)
    
    for url in urls:
        try:
            # Replicate MCP add_source logic
            spec = await build_source_spec_from_input(
                url,
                auth_provider=runtime.auth_provider,
            )
            await runner.add_source_spec(tenant_id, spec, input_value=url)
            ok += 1
        except Exception as e:
            logger.exception("failed_to_add_source", url=url, error=str(e))
            fail += 1
            
    await state.clear()
    res_text = f"✅ Добавлено {ok} источников."
    if fail > 0:
        res_text += f"\n⚠️ {fail} не удалось добавить."
    
    res_text += "\n\n/sources — посмотреть список\n/run — запустить"
    await status_msg.edit_text(res_text)

@router.callback_query(SourceAction.filter(F.action == "run"))
async def callback_run_from_sources(callback: CallbackQuery, runner: "TenantRunner", bot: Bot) -> None:
    from adapters.telegram_bot.handlers.pipeline import run_pipeline_for_chat
    await callback.answer("Запускаю...")
    msg = callback.message
    if isinstance(msg, Message):
        await run_pipeline_for_chat(msg, runner, bot)
