import asyncio
import re
from contextlib import asynccontextmanager
from io import BytesIO
from typing import TYPE_CHECKING, Any

import structlog
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from job_ftch.adapters.telegram_bot.fsm.states import AddingSources
from job_ftch.adapters.telegram_bot.utils import (
    require_admin_callback,
    require_admin_message,
    safe_error_reply,
)
from job_ftch.application.source_inputs import build_source_spec_from_input
from job_ftch.infrastructure.document_parser import parse_document

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

    from job_ftch.adapters.telegram_bot.config import TelegramBotConfig
    from job_ftch.application.tenant_runner import TenantRunner

logger = structlog.get_logger(__name__)
router = Router(name="sources")

# Each source add runs a real network probe (site fingerprint + a
# sample DOM-extraction attempt across every discovered link — up to
# ~100+ requests for a content-rich domain). Awaiting this inline in
# the handler used to tie up the bot's single event loop for the
# whole batch — for 16 sources that ran past an hour and made the bot
# deaf to every other update, including button presses (Telegram
# expires those in seconds). Track background tasks so they aren't
# garbage-collected mid-flight.
_background_tasks: set[asyncio.Task[None]] = set()


class SourceAction(CallbackData, prefix="src"):
    action: str


class SourceItemAction(CallbackData, prefix="srci"):
    action: str
    index: int


class SourcePageAction(CallbackData, prefix="srcpg"):
    page: int


# Telegram renders every inline button, so the keyboard must not grow with the number of
# sources. Eight toggles in two columns keeps the listing text visible above it.
_SOURCES_PAGE_SIZE = 8
_TELEGRAM_TEXT_LIMIT = 4096
_SOURCES_TEXT_SOFT_LIMIT = 3600


def _status_icon(status: str) -> str:
    return {
        "healthy": "✅",
        "failing": "⚠️",
        "degraded": "📉",
        "paused": "⏸",
        "disabled": "⏹",
        "pending": "🕓",
    }.get(status, "•")


def _truncate_error(value: str | None, limit: int = 90) -> str | None:
    if not value:
        return None
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _truncate_inline(value: object, limit: int = 180) -> str:
    compact = " ".join(str(value).split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _format_source_line(source: dict[str, Any], *, index: int) -> str:
    url = source.get("locator") or source.get("source_name") or source.get("source_id", "???")
    kind = str(source.get("source_kind", ""))
    if url and not str(url).startswith("http") and kind in ("telegram_channel", "telegram_group"):
        url = f"https://t.me/{str(url).lstrip('@')}"
    url = _truncate_inline(url, limit=220)

    status = str(source.get("status", "pending"))
    parts = [f"{_status_icon(status)} #{index}. {url}"]
    details: list[str] = []
    failure_streak = int(source.get("failure_streak", 0) or 0)
    last_failed = int(source.get("last_failed", 0) or 0)
    last_emitted = int(source.get("last_emitted", 0) or 0)
    degraded = bool(source.get("degraded", False))
    requirements = source.get("requirements") or {}
    assessment = source.get("assessment") or {}
    recommended_monitors = assessment.get("recommended_monitors") or []
    if bool(requirements.get("browser_required")):
        details.append("browser")
    if recommended_monitors:
        details.append(" -> ".join(str(m) for m in recommended_monitors[:2]))
    if failure_streak > 0:
        details.append(f"streak={failure_streak}")
    if last_failed > 0:
        details.append(f"failed={last_failed}")
    if last_emitted > 0:
        details.append(f"emitted={last_emitted}")
    if degraded:
        details.append("drift")
    if details:
        parts.append(f" ({', '.join(details)})")
    error = _truncate_error(source.get("last_error"))
    if error:
        parts.append(f"\n   └─ {error}")
    browser_hint = requirements.get("browser_setup_hint")
    if browser_hint and str(source.get("status", "pending")) in {"failing", "degraded", "pending"}:
        parts.append(f"\n   └─ {_truncate_inline(browser_hint, limit=120)}")
    return "".join(parts)


def _compact_source_line(source: dict[str, Any], *, index: int) -> str:
    status = str(source.get("status", "pending"))
    name = source.get("source_name") or source.get("source_id") or source.get("locator", "???")
    return f"{_status_icon(status)} #{index}. {_truncate_inline(name, limit=90)}"


def _extract_source_inputs(text: str) -> list[str]:
    parts = re.split(r"[\s\n]+", text)
    source_type_prefixes = (
        "career_site:",
        "rss:",
        "rss_feed:",
        "site:",
        "telegram:",
        "telegram_channel:",
        "telegram_group:",
        "tg:",
        "tg_channel:",
        "tg_group:",
        "url:",
    )
    inputs = []
    for part in parts:
        value = part.strip()
        if not value:
            continue
        if value.startswith(("http", "t.me/", "@")) or value.startswith(source_type_prefixes):
            inputs.append(value)
    return inputs


async def _add_source_inputs(
    *,
    message: Message,
    state: FSMContext,
    runner: "TenantRunner",
    inputs: list[str],
) -> None:
    if not inputs:
        await message.answer("Не нашёл URL. Попробуй ещё раз или /cancel")
        return

    user_id = str(message.from_user.id) if message.from_user else None
    tenant_id = await runner.get_selected_tenant_id(user_id)
    status_msg = await message.answer(
        f"⏳ Добавляю {len(inputs)} источников в фоне (это может занять время на"
        f" сайтах с большим числом ссылок)..."
    )
    runtime = runner.get_runtime(tenant_id)
    added_by = str(message.from_user.id) if message.from_user else None

    # Clear the FSM state immediately — the user is done submitting
    # input. The actual per-source network probe runs in the
    # background so it can't block the bot's event loop (and every
    # other user's interactions) for as long as it takes.
    await state.clear()

    task = asyncio.create_task(
        _process_source_inputs_background(
            status_msg=status_msg,
            runner=runner,
            runtime=runtime,
            tenant_id=tenant_id,
            added_by=added_by,
            inputs=inputs,
        ),
        name=f"add_sources:{tenant_id}:{added_by}",
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _process_source_inputs_background(
    *,
    status_msg: Message,
    runner: "TenantRunner",
    runtime: Any,
    tenant_id: str,
    added_by: str | None,
    inputs: list[str],
) -> None:
    ok, fail = 0, 0

    @asynccontextmanager
    async def telegram_client_factory(
        _auth_source_id: str | None, _auth_provider: object
    ) -> "AsyncGenerator[object, None]":
        from job_ftch.infrastructure.sources.telegram import _build_telegram_client

        settings = runtime.settings.model_copy(
            update={"telegram_entity": runtime.settings.telegram_entity or "probe"}
        )
        client = _build_telegram_client(settings)
        async with client as managed_client:
            yield managed_client

    for raw_input in inputs:
        try:
            spec = await build_source_spec_from_input(
                raw_input,
                auth_provider=runtime.auth_provider,
                telegram_client_factory=telegram_client_factory,
            )
            await runner.add_source_spec(
                tenant_id,
                spec,
                input_value=raw_input,
                added_via="telegram_bot",
                added_by=added_by,
            )
            ok += 1
        except Exception as exc:  # noqa: BLE001 - report and continue with the rest
            fail += 1
            logger.warning(
                "failed_to_add_source",
                tenant_id=tenant_id,
                input=raw_input,
                error=str(exc),
            )

    res_text = f"✅ Добавлено {ok} источников."
    if fail > 0:
        res_text += f"\n⚠️ {fail} не удалось добавить (см. логи)."

    res_text += "\n\n/sources — посмотреть список\n/run — запустить"
    try:
        await status_msg.edit_text(res_text)
    except Exception:  # noqa: BLE001 - message may be too new to edit; not fatal
        logger.warning("add_sources_status_edit_failed", tenant_id=tenant_id)


_KIND_LABELS = {
    "telegram_channel": "📡 Telegram каналы",
    "telegram_group": "👥 Telegram группы",
    "web": "🌐 Web",
    "career_site": "🌐 Web (career site)",
    "api": "🔌 API",
    "rss": "📰 RSS",
    "rss_feed": "📰 RSS",
    "other": "📦 Другое",
}


def page_count(total: int) -> int:
    """Number of toggle-button pages for ``total`` sources (at least one)."""
    if total <= 0:
        return 1
    return (total + _SOURCES_PAGE_SIZE - 1) // _SOURCES_PAGE_SIZE


def build_sources_view(
    payloads: "Sequence[dict[str, Any]]", *, page: int = 0
) -> tuple[str, InlineKeyboardMarkup]:
    """Render the source inventory with a bounded keyboard.

    One toggle button per source stacked a 17-source tenant into a 20-row keyboard that
    buried the listing itself. Keep both the text and toggles page-bounded so Telegram's
    4096-character message limit cannot break tenants with many sources.
    """
    source_indexes = {
        str(payload.get("source_id")): index for index, payload in enumerate(payloads)
    }
    status_counts: dict[str, int] = {}
    for payload in payloads:
        status = str(payload.get("status", "pending"))
        status_counts[status] = status_counts.get(status, 0) + 1

    total_pages = page_count(len(payloads))
    # Clamp instead of raising: a stale keyboard from a previous listing can still
    # deliver a page index that no longer exists after sources were removed.
    current_page = max(0, min(page, total_pages - 1))
    start = current_page * _SOURCES_PAGE_SIZE
    window = list(enumerate(payloads))[start : start + _SOURCES_PAGE_SIZE]
    grouped_window: dict[str, list[dict[str, Any]]] = {}
    for _index, payload in window:
        kind = str(payload.get("source_kind", "other"))
        grouped_window.setdefault(kind, []).append(payload)

    lines = []
    summary_parts = [f"Всего: {len(payloads)}"]
    for status in ("healthy", "failing", "degraded", "paused", "pending", "disabled"):
        count = status_counts.get(status, 0)
        if count > 0:
            summary_parts.append(f"{_status_icon(status)} {count}")
    lines.append("Статус источников: " + "  ".join(summary_parts))
    if total_pages > 1 and window:
        lines.append(
            f"Показаны #{window[0][0] + 1}–#{window[-1][0] + 1} "
            f"из {len(payloads)} (страница {current_page + 1}/{total_pages})"
        )
    lines.append("")

    for kind, sources in grouped_window.items():
        label = _KIND_LABELS.get(kind, kind.title())
        lines.append(f"{label} ({len(sources)}):")
        for source in sources:
            source_index = source_indexes.get(str(source.get("source_id")), 0) + 1
            lines.append(_format_source_line(source, index=source_index))
        lines.append("")

    builder = InlineKeyboardBuilder()
    for index, source in window:
        enabled = bool(source.get("enabled", True))
        action = "disable" if enabled else "enable"
        label = "⏹" if enabled else "▶"
        builder.button(
            text=f"{label} #{index + 1}",
            callback_data=SourceItemAction(action=action, index=index),
        )
    toggle_rows = [2] * ((len(window) + 1) // 2)

    nav_row = 0
    if total_pages > 1:
        builder.button(
            text="◀",
            callback_data=SourcePageAction(page=(current_page - 1) % total_pages),
        )
        builder.button(
            text=f"{current_page + 1}/{total_pages}", callback_data=SourceAction(action="noop")
        )
        builder.button(
            text="▶",
            callback_data=SourcePageAction(page=(current_page + 1) % total_pages),
        )
        nav_row = 3

    builder.button(text="➕ Добавить источники", callback_data=SourceAction(action="add"))
    builder.button(text="▶ Запустить пайплайн", callback_data=SourceAction(action="run"))
    builder.button(text="🗑 Удалить все", callback_data=SourceAction(action="clear"))

    layout = [*toggle_rows]
    if nav_row:
        layout.append(nav_row)
    layout.extend([1, 1, 1])
    builder.adjust(*layout)

    text = "\n".join(lines)
    if len(text) > _SOURCES_TEXT_SOFT_LIMIT:
        compact_lines = lines[:2] if len(lines) >= 2 else list(lines)
        compact_lines.append("")
        compact_lines.append("Список сокращён: слишком много длинных URL/диагностик.")
        for index, source in window:
            compact_lines.append(_compact_source_line(source, index=index + 1))
        text = "\n".join(compact_lines)
    if len(text) >= _TELEGRAM_TEXT_LIMIT:
        text = text[: _TELEGRAM_TEXT_LIMIT - 2] + "…"

    return text, builder.as_markup()


@router.message(Command("sources"))
async def cmd_sources(
    message: Message,
    runner: "TenantRunner",
    config: "TelegramBotConfig",
    user_id_override: int | None = None,
) -> None:
    if not await require_admin_message(message, config, user_id_override=user_id_override):
        return
    user_id = str(
        user_id_override
        if user_id_override is not None
        else (message.from_user.id if message.from_user else 0)
    )
    tenant_id = await runner.get_selected_tenant_id(user_id)
    payloads = await runner.list_sources(tenant_id)

    if not payloads:
        builder = InlineKeyboardBuilder()
        builder.button(text="➕ Добавить источники", callback_data=SourceAction(action="add"))
        await message.answer(
            "Источников нет. Нажми + чтобы добавить.", reply_markup=builder.as_markup()
        )
        return

    text, markup = build_sources_view(payloads)
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(SourcePageAction.filter())
async def callback_sources_page(
    callback: CallbackQuery,
    callback_data: SourcePageAction,
    runner: "TenantRunner",
    config: "TelegramBotConfig",
) -> None:
    if not await require_admin_callback(callback, config):
        return
    user_id = str(callback.from_user.id if callback.from_user else 0)
    tenant_id = await runner.get_selected_tenant_id(user_id)
    payloads = await runner.list_sources(tenant_id)
    message = callback.message
    if isinstance(message, Message) and payloads:
        text, markup = build_sources_view(payloads, page=callback_data.page)
        try:
            await message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:  # noqa: BLE001 - identical content or an expired message
            logger.debug("sources_page_edit_skipped", tenant_id=tenant_id)
    await callback.answer()


@router.callback_query(SourceAction.filter(F.action == "noop"))
async def callback_sources_noop(callback: CallbackQuery) -> None:
    """The page indicator is a label; acknowledge so the client stops the spinner."""
    await callback.answer()


@router.callback_query(SourceAction.filter(F.action == "add"))
async def callback_add_sources(
    callback: CallbackQuery, state: FSMContext, config: "TelegramBotConfig"
) -> None:
    if not await require_admin_callback(callback, config):
        return
    await state.set_state(AddingSources.waiting)
    msg = callback.message
    if isinstance(msg, Message):
        await msg.answer(
            "Кидай URL источников — каждый с новой строки или через пробел.\n\n"
            "Можно прислать .txt/.md файл со списком источников.\n\n"
            "Примеры:\n"
            "https://t.me/ai_jobs\n"
            "telegram_group:https://t.me/python_jobs\n"
            "rss:https://example.com/feed.xml\n"
            "https://hh.ru/search/vacancy?text=python"
        )
    await callback.answer()


@router.message(AddingSources.waiting, F.text, ~F.text.startswith("/"))
async def handle_sources_text(
    message: Message,
    state: FSMContext,
    runner: "TenantRunner",
    config: "TelegramBotConfig",
) -> None:
    if not await require_admin_message(message, config):
        return
    await _add_source_inputs(
        message=message,
        state=state,
        runner=runner,
        inputs=_extract_source_inputs(message.text or ""),
    )


@router.message(AddingSources.waiting, F.document)
async def handle_sources_document(
    message: Message,
    state: FSMContext,
    runner: "TenantRunner",
    bot: Bot,
    config: "TelegramBotConfig",
) -> None:
    if not await require_admin_message(message, config):
        return
    document = message.document
    if not document:
        return
    try:
        file = await bot.get_file(document.file_id)
        content = BytesIO()
        if file.file_path:
            await bot.download_file(file.file_path, content)
        raw_bytes = content.getvalue()
        if not raw_bytes:
            await message.answer("Файл пустой или слишком большой. Пришли список текстом.")
            return
        text = parse_document(raw_bytes, document.file_name or "sources.txt")
    except Exception as exc:
        await safe_error_reply(message, exc, "failed_to_parse_sources_document")
        return
    await _add_source_inputs(
        message=message,
        state=state,
        runner=runner,
        inputs=_extract_source_inputs(text),
    )


@router.callback_query(SourceAction.filter(F.action == "run"))
async def callback_run_from_sources(
    callback: CallbackQuery,
    runner: "TenantRunner",
    bot: Bot,
    config: "TelegramBotConfig",
) -> None:
    if not await require_admin_callback(callback, config):
        return
    from job_ftch.adapters.telegram_bot.handlers.pipeline import run_pipeline_for_chat

    await callback.answer("Запускаю...")
    msg = callback.message
    if isinstance(msg, Message):
        await run_pipeline_for_chat(msg, runner, bot, user_id_override=callback.from_user.id)


@router.callback_query(SourceAction.filter(F.action == "clear"))
async def callback_clear_sources(
    callback: CallbackQuery, runner: "TenantRunner", config: "TelegramBotConfig"
) -> None:
    if not await require_admin_callback(callback, config):
        return
    tenant_id = await runner.get_selected_tenant_id(str(callback.from_user.id))
    await runner.clear_sources(tenant_id)
    await callback.answer("Все источники удалены/отключены")
    msg = callback.message
    if isinstance(msg, Message):
        builder = InlineKeyboardBuilder()
        builder.button(text="➕ Добавить источники", callback_data=SourceAction(action="add"))
        await msg.edit_text(
            "Источников нет. Нажми + чтобы добавить.", reply_markup=builder.as_markup()
        )


@router.callback_query(SourceItemAction.filter())
async def callback_toggle_source(
    callback: CallbackQuery,
    callback_data: SourceItemAction,
    runner: "TenantRunner",
    config: "TelegramBotConfig",
) -> None:
    if not await require_admin_callback(callback, config):
        return
    tenant_id = await runner.get_selected_tenant_id(str(callback.from_user.id))
    payloads = await runner.list_sources(tenant_id)
    if callback_data.index < 0 or callback_data.index >= len(payloads):
        await callback.answer("Источник не найден.", show_alert=True)
        return

    payload = payloads[callback_data.index]
    source_id = str(payload["source_id"])
    if callback_data.action == "disable":
        await runner.disable_source(tenant_id, source_id)
        await callback.answer(f"Источник #{callback_data.index + 1} отключен")
    elif callback_data.action == "enable":
        spec_payload = payload.get("spec")
        if not isinstance(spec_payload, dict):
            await callback.answer("Не удалось включить источник.", show_alert=True)
            return
        from pydantic import TypeAdapter

        from job_ftch.domain.source_spec import SourceSpec

        spec: SourceSpec = TypeAdapter(SourceSpec).validate_python(spec_payload)
        await runner.add_source_spec(tenant_id, spec, added_via="telegram_bot")
        await callback.answer(f"Источник #{callback_data.index + 1} включен")
    else:
        await callback.answer("Неизвестное действие.", show_alert=True)
        return

    if isinstance(callback.message, Message):
        await cmd_sources(
            callback.message,
            runner,
            config,
            user_id_override=callback.from_user.id,
        )
