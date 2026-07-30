"""Reader feedback on published vacancies — /feedback and the card button.

The button is an evidence collector, not a control: pressing it never edits or withdraws
a published vacancy. Promotion into profile negatives is a separate, explicit admin step,
because unreviewed negatives were measured to cost both precision and recall.
"""

from typing import TYPE_CHECKING, Any, cast

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from job_ftch.adapters.telegram_bot.utils import (
    require_admin_callback,
    require_admin_message,
    safe_error_reply,
)
from job_ftch.application.publish_ledger import extract_publish_job_id, load_publish_ledger
from job_ftch.application.vacancy_feedback import (
    build_feedback,
    clear_feedback,
    get_feedback_audience,
    load_feedback,
    may_submit_feedback,
    promotable_texts,
    record_feedback,
    set_feedback_audience,
    summarize_feedback,
)
from job_ftch.domain.feedback import FeedbackAudience

if TYPE_CHECKING:
    from job_ftch.adapters.telegram_bot.config import TelegramBotConfig
    from job_ftch.application.tenant_runner import TenantRunner

logger = structlog.get_logger(__name__)
router = Router(name="feedback")

# Telegram caps callback_data at 64 bytes including the "fbk:" prefix, and real publish
# ids are 64-char SHA-256 hex, so the full id cannot travel in a button. The card carries
# a prefix instead; 16 hex chars are 64 bits of the digest, which cannot realistically
# collide inside one channel's publication history.
_JOB_ID_PREFIX = 16
_CALLBACK_DATA_LIMIT = 64


class FeedbackAction(CallbackData, prefix="fbk"):
    job_id: str


class FeedbackAdminAction(CallbackData, prefix="fbadm"):
    action: str


def _excerpt(job: object) -> str:
    for attribute in ("description_clean", "description", "description_raw"):
        value = getattr(job, attribute, None)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:4000]
    return ""


def build_feedback_markup(job: object) -> InlineKeyboardMarkup | None:
    """Attach the off-profile control to a published card.

    Typed as ``object`` to match ``extract_publish_job_id``: both read the publish
    identity by duck typing, and a job whose row has already expired still needs a card.
    """
    job_id = extract_publish_job_id(job)
    if not job_id:
        # Without a stable id a press cannot be attributed, and an unattributable
        # button is worse than none: it looks like it worked.
        return None
    action = FeedbackAction(job_id=job_id[:_JOB_ID_PREFIX])
    if len(action.pack().encode()) > _CALLBACK_DATA_LIMIT:
        logger.warning("feedback_callback_data_too_long", job_id=job_id)
        return None
    builder = InlineKeyboardBuilder()
    builder.button(text="🚫 Не по профилю", callback_data=action)
    return builder.as_markup()


async def _resolve_published_job(
    runner: "TenantRunner", store: object, tenant_id: str, job_id_prefix: str
) -> object | None:
    """Recover the full job behind a button prefix.

    The publish ledger already records the full id of everything the channel posted, so it
    is the precise place to expand a prefix — no scan of the job store required.
    """
    try:
        ledger = await load_publish_ledger(cast("Any", store))
    except Exception:  # noqa: BLE001 - enrichment is optional, the verdict is not
        return None
    full_id = next((known for known in reversed(ledger) if known.startswith(job_id_prefix)), None)
    if full_id is None:
        return None
    try:
        return await runner.get_job(full_id, tenant_id=tenant_id)
    except Exception:  # noqa: BLE001 - a missing job must not lose the verdict
        return None


_AUDIENCE_CHOICES = (
    (FeedbackAudience.OFF, "Выключить"),
    (FeedbackAudience.ADMIN, "Только админы"),
    (FeedbackAudience.ALL, "Все читатели"),
)


def _admin_markup(audience: FeedbackAudience) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for choice, label in _AUDIENCE_CHOICES:
        selected = "🔘 " if choice is audience else ""
        builder.button(
            text=f"{selected}{label}",
            callback_data=FeedbackAdminAction(action=f"set_{choice.value}"),
        )
    builder.button(text="⬇️ В негативы", callback_data=FeedbackAdminAction(action="promote"))
    builder.button(text="🗑 Очистить", callback_data=FeedbackAdminAction(action="clear"))
    builder.adjust(3, 1, 1)
    return builder.as_markup()


def render_summary(summary: Any, *, audience: FeedbackAudience, threshold: int = 2) -> str:
    lines = [
        f"Обратная связь: {audience.label}",
        "",
    ]
    if summary.is_empty:
        lines.append("Отметок пока нет.")
        return "\n".join(lines)

    lines.append(f"Всего отметок: {summary.total} по {summary.distinct_jobs} вакансиям")
    if summary.by_source:
        pairs = ", ".join(f"{name}: {count}" for name, count in list(summary.by_source.items())[:6])
        lines.append(f"По источникам: {pairs}")
    lines.append("")
    lines.append("Чаще всего отмечали:")
    for tally in summary.top_jobs[:8]:
        title = tally.title or tally.job_id
        mark = " ✅" if tally.votes >= threshold else ""
        lines.append(f"• {tally.votes}× {title[:70]}{mark}")
    lines.append("")
    lines.append(f"✅ — набрали порог {threshold}, готовы к переносу в негативы.")
    return "\n".join(lines)


async def _tenant_and_store(runner: "TenantRunner", user_id: str) -> tuple[str, Any]:
    tenant_id = await runner.get_selected_tenant_id(user_id)
    return tenant_id, runner.get_runtime(tenant_id).store


@router.message(Command("feedback"))
async def cmd_feedback(
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
    try:
        tenant_id, store = await _tenant_and_store(runner, user_id)
        audience = await get_feedback_audience(store, tenant_id)
        summary = summarize_feedback(tenant_id, await load_feedback(store, tenant_id))
        await message.answer(
            render_summary(summary, audience=audience), reply_markup=_admin_markup(audience)
        )
    except Exception as error:  # noqa: BLE001 - surfaced to the admin
        await safe_error_reply(message, error, "cmd_feedback")


@router.callback_query(FeedbackAdminAction.filter())
async def callback_feedback_admin(
    callback: CallbackQuery,
    callback_data: FeedbackAdminAction,
    runner: "TenantRunner",
    config: "TelegramBotConfig",
) -> None:
    if not await require_admin_callback(callback, config):
        return
    user_id = str(callback.from_user.id if callback.from_user else 0)
    message = callback.message
    tenant_id, store = await _tenant_and_store(runner, user_id)
    action = callback_data.action

    if action.startswith("set:") or action.startswith("set_"):
        value = action.split(":", 1)[1] if action.startswith("set:") else action[4:]
        try:
            await set_feedback_audience(store, tenant_id, FeedbackAudience(value))
        except ValueError:
            logger.warning("feedback_audience_unknown", tenant_id=tenant_id, action=action)
    elif action == "clear":
        removed = await clear_feedback(store, tenant_id)
        if isinstance(message, Message):
            await message.answer(f"Очищено отметок: {removed}.")
    elif action == "promote":
        summary = summarize_feedback(tenant_id, await load_feedback(store, tenant_id))
        texts = promotable_texts(summary)
        if isinstance(message, Message):
            if not texts:
                await message.answer(
                    "Пока нечего переносить: нужна отметка минимум от двух разных читателей."
                )
            else:
                await message.answer(
                    f"Готово к переносу: {len(texts)} вакансий.\n"
                    "Отправь их через /negative_job, чтобы они стали негативными примерами "
                    "профиля. Автоматически это не делается: непроверенные негативы "
                    "снижают и точность, и полноту."
                )

    audience = await get_feedback_audience(store, tenant_id)
    summary = summarize_feedback(tenant_id, await load_feedback(store, tenant_id))
    if isinstance(message, Message):
        try:
            await message.edit_text(
                render_summary(summary, audience=audience), reply_markup=_admin_markup(audience)
            )
        except Exception:  # noqa: BLE001 - unchanged text or an expired message
            logger.debug("feedback_summary_edit_skipped", tenant_id=tenant_id)
    await callback.answer()


@router.callback_query(FeedbackAction.filter())
async def callback_vacancy_feedback(
    callback: CallbackQuery,
    callback_data: FeedbackAction,
    runner: "TenantRunner",
    config: "TelegramBotConfig",
) -> None:
    """Record one verdict if the configured audience allows this presser.

    The card is identical for everyone who sees the channel, so the button's presence
    cannot encode permission — authorization happens here, when the press arrives.
    """
    presser = callback.from_user
    user_id = str(presser.id if presser else 0)
    # The channel belongs to the tenant, not to whoever is reading it, so resolve the
    # default tenant rather than the presser's selection.
    tenant_id = await runner.get_selected_tenant_id(None)
    store = runner.get_runtime(tenant_id).store

    audience = await get_feedback_audience(store, tenant_id)
    is_admin = presser is not None and presser.id in config.admin_user_ids
    if not may_submit_feedback(audience, is_admin=is_admin):
        await callback.answer(
            "Сбор обратной связи выключен."
            if audience is FeedbackAudience.OFF
            else "Отмечать вакансии могут только админы."
        )
        return

    title = ""
    url = ""
    source_name = ""
    excerpt = ""
    job = await _resolve_published_job(runner, store, tenant_id, callback_data.job_id)
    if job is not None:
        title = str(getattr(job, "title", "") or "")
        url = str(getattr(job, "canonical_url", "") or "")
        source_name = str(getattr(job, "source_name", "") or "")
        excerpt = _excerpt(job)

    stored, _ = await record_feedback(
        store,
        build_feedback(
            tenant_id=tenant_id,
            job_id=callback_data.job_id,
            user_id=user_id,
            title=title,
            url=url,
            source_name=source_name,
            excerpt=excerpt,
        ),
    )
    logger.info(
        "vacancy_feedback_recorded",
        tenant_id=tenant_id,
        job_id=callback_data.job_id,
        stored=stored,
    )
    await callback.answer("Спасибо, отметил." if stored else "Уже отмечено.")


@router.callback_query(F.data == "fbk:noop")
async def callback_feedback_noop(callback: CallbackQuery) -> None:
    await callback.answer()
