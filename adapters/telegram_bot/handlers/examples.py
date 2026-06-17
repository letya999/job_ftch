import asyncio
from io import BytesIO
from typing import TYPE_CHECKING, Literal

import structlog
from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from adapters.telegram_bot.fsm.states import AddingExamples
from job_ftch.application.profile_inputs import (
    build_profile_from_resume_text_async,
    embed_profile_examples,
    merge_resume_profile,
    remove_example_from_profile,
)
from job_ftch.domain import (
    CandidateIdentity,
    CandidateProfile,
    CandidateResumeSnapshot,
    ManagedCandidateProfile,
    SearchProfile,
)
from job_ftch.infrastructure.document_parser import parse_document

if TYPE_CHECKING:
    from job_ftch.application.tenant_runner import TenantRunner

logger = structlog.get_logger(__name__)
router = Router(name="examples")

_user_locks: dict[str, asyncio.Lock] = {}


def _get_user_lock(user_id: str) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


class ExampleNav(CallbackData, prefix="exnav"):
    action: str
    idx: int = 0


def _profile_id(user_id: str) -> str:
    return f"user_{user_id}"


async def _get_tenant_and_profile(
    runner: "TenantRunner", from_user_id: int | None
) -> tuple[str, str, str]:
    tenant_ids = runner.tenant_ids()
    tenant_id = tenant_ids[0] if tenant_ids else "default"
    user_id_str = str(from_user_id) if from_user_id is not None else "0"
    profile_id = _profile_id(user_id_str)
    return tenant_id, user_id_str, profile_id


def _get_example_counts(profile: ManagedCandidateProfile | None) -> tuple[int, int]:
    if not profile or not profile.profile.search_profiles:
        return 0, 0
    sp = profile.profile.search_profiles[0]
    return len(sp.positive_example_texts), len(sp.negative_example_texts)


@router.message(Command("positive"))
async def cmd_positive(
    message: Message, state: FSMContext, runner: "TenantRunner", user_id_override: int | None = None
) -> None:
    await state.set_state(AddingExamples.positive)
    resolved_uid = (
        user_id_override
        if user_id_override is not None
        else (message.from_user.id if message.from_user else None)
    )
    tenant_id, user_id, profile_id = await _get_tenant_and_profile(runner, resolved_uid)
    profile = await runner.get_candidate_profile(tenant_id, user_id, profile_id)
    pos, neg = _get_example_counts(profile)
    await message.answer(
        f"Кидай PDF или текст резюме (до 10 штук). Отправь /done когда закончишь.\n\n"
        f"Текущий счёт: {pos}+ / {neg}−"
    )


@router.message(Command("negative"))
async def cmd_negative(
    message: Message, state: FSMContext, runner: "TenantRunner", user_id_override: int | None = None
) -> None:
    await state.set_state(AddingExamples.negative)
    resolved_uid = (
        user_id_override
        if user_id_override is not None
        else (message.from_user.id if message.from_user else None)
    )
    tenant_id, user_id, profile_id = await _get_tenant_and_profile(runner, resolved_uid)
    profile = await runner.get_candidate_profile(tenant_id, user_id, profile_id)
    pos, neg = _get_example_counts(profile)
    await message.answer(
        f"Кидай PDF или текст резюме которые НЕ подходят (до 10). /done когда закончишь.\n\n"
        f"Текущий счёт: {pos}+ / {neg}−"
    )


@router.message(Command("done"), StateFilter(AddingExamples))
async def cmd_done(message: Message, state: FSMContext, runner: "TenantRunner") -> None:
    tenant_id, user_id, profile_id = await _get_tenant_and_profile(
        runner, message.from_user.id if message.from_user else None
    )
    profile = await runner.get_candidate_profile(tenant_id, user_id, profile_id)
    pos, neg = _get_example_counts(profile)
    await state.clear()
    await message.answer(
        f"✅ Готово. Примеров: {pos}+ / {neg}−\n\n"
        f"/examples — посмотреть все\n"
        f"/sources — настроить источники"
    )


@router.message(StateFilter(AddingExamples), F.document)
async def handle_document_example(
    message: Message, state: FSMContext, runner: "TenantRunner", bot: Bot
) -> None:
    document = message.document
    if not document:
        return

    curr_state = await state.get_state()
    is_negative = curr_state == AddingExamples.negative.state

    tenant_id, user_id, profile_id = await _get_tenant_and_profile(
        runner, message.from_user.id if message.from_user else None
    )

    status_msg = await message.answer("⏳ Обработка документа...")

    lock = _get_user_lock(user_id)
    async with lock:
        try:
            file = await bot.get_file(document.file_id)
            content = BytesIO()
            if file.file_path:
                await bot.download_file(file.file_path, content)

            text = parse_document(content.getvalue(), document.file_name or "resume.txt")

            runtime = runner.get_runtime(tenant_id)
            existing = await runner.get_candidate_profile(tenant_id, user_id, profile_id)

            extracted = await build_profile_from_resume_text_async(
                text, user_id=user_id, profile_id=profile_id, llm_provider=runtime.llm_provider
            )

            if existing:
                managed = merge_resume_profile(existing, extracted, is_negative=is_negative)
            else:
                managed = merge_resume_profile(extracted, extracted, is_negative=is_negative)

            if runtime.embedding_provider:
                managed = await embed_profile_examples(managed, runtime.embedding_provider)

            await runner.save_candidate_profile(tenant_id, managed)
            await runner.set_active_candidate_profile(tenant_id, user_id, profile_id)

            pos, neg = _get_example_counts(managed)
            count = neg if is_negative else pos

            roles_preview = "не определено"
            if managed.profile.search_profiles:
                roles_preview = ", ".join(managed.profile.search_profiles[0].target_roles[:3])

            is_limit = count >= 10

        except Exception as e:
            logger.exception("failed_to_process_document", error=str(e))
            await status_msg.edit_text(f"❌ Ошибка: {str(e)}")
            return

    # Reply outside the lock
    if is_limit:
        await status_msg.edit_text(f"✅ #{count} добавлен (достигнут лимит 10). Отправь /done.")
        await state.clear()
    else:
        await status_msg.edit_text(
            f"✅ #{count} добавлен. Ролей: {roles_preview}. Ещё {10 - count} слотов."
        )


@router.message(StateFilter(AddingExamples), F.text, ~F.text.startswith("/"))
async def handle_text_example(message: Message, state: FSMContext, runner: "TenantRunner") -> None:
    text = (message.text or "").strip()
    if len(text) < 30:
        await message.answer("Текст слишком короткий, кинь нормальное описание.")
        return

    curr_state = await state.get_state()
    is_negative = curr_state == AddingExamples.negative.state

    tenant_id, user_id, profile_id = await _get_tenant_and_profile(
        runner, message.from_user.id if message.from_user else None
    )

    lock = _get_user_lock(user_id)
    async with lock:
        try:
            existing = await runner.get_candidate_profile(tenant_id, user_id, profile_id)

            # Create minimal profile if none
            if not existing:
                existing = ManagedCandidateProfile(
                    user_id=user_id,
                    profile_id=profile_id,
                    profile=CandidateProfile(
                        identity=CandidateIdentity(candidate_id=user_id, display_name="User"),
                        search_profiles=(SearchProfile(),),
                    ),
                )

            # Wrap text into extracted profile to use merge_resume_profile
            extracted = ManagedCandidateProfile(
                user_id=user_id,
                profile_id=profile_id,
                profile=CandidateProfile(
                    identity=CandidateIdentity(candidate_id=user_id, display_name="User"),
                    resume=CandidateResumeSnapshot(raw_text=text),
                    search_profiles=(SearchProfile(),),
                ),
            )

            managed = merge_resume_profile(existing, extracted, is_negative=is_negative)

            runtime = runner.get_runtime(tenant_id)
            if runtime.embedding_provider:
                managed = await embed_profile_examples(managed, runtime.embedding_provider)

            await runner.save_candidate_profile(tenant_id, managed)
            await runner.set_active_candidate_profile(tenant_id, user_id, profile_id)

            pos, neg = _get_example_counts(managed)
            count = neg if is_negative else pos

            is_limit = count >= 10

        except Exception as e:
            logger.exception("failed_to_process_text", error=str(e))
            await message.answer(f"❌ Ошибка: {str(e)}")
            return

    # Reply outside the lock
    await message.answer(f"✅ Текстовый пример #{count} добавлен.")
    if is_limit:
        await message.answer("Достигнут лимит 10 примеров. Отправь /done.")
        await state.clear()


@router.message(Command("examples"))
async def cmd_examples(
    message: Message, runner: "TenantRunner", user_id_override: int | None = None
) -> None:
    resolved_uid = (
        user_id_override
        if user_id_override is not None
        else (message.from_user.id if message.from_user else None)
    )
    tenant_id, user_id, profile_id = await _get_tenant_and_profile(runner, resolved_uid)
    profile = await runner.get_candidate_profile(tenant_id, user_id, profile_id)
    pos, neg = _get_example_counts(profile)

    if pos == 0 and neg == 0:
        await message.answer(
            "Примеров ещё нет.\n\n/positive — добавить позитивные\n/negative — добавить негативные"
        )
        return

    builder = InlineKeyboardBuilder()
    builder.button(text=f"📗 Позитивные ({pos})", callback_data=ExampleNav(action="show_pos"))
    builder.button(text=f"📕 Негативные ({neg})", callback_data=ExampleNav(action="show_neg"))
    builder.button(text="🗑 Удалить все", callback_data=ExampleNav(action="del_all"))
    builder.adjust(2, 1)

    await message.answer("Ваши примеры:", reply_markup=builder.as_markup())


async def _show_example_page(
    callback: CallbackQuery, examples: tuple[str, ...], idx: int, kind: Literal["pos", "neg"]
) -> None:
    msg = callback.message
    if not isinstance(msg, Message):
        return

    text = examples[idx]
    display_text = text[:800] + ("..." if len(text) > 800 else "")
    emoji = "📗" if kind == "pos" else "📕"

    builder = InlineKeyboardBuilder()
    builder.button(text="◀", callback_data=ExampleNav(action=f"prev_{kind}", idx=idx))
    builder.button(text=f"{idx + 1} / {len(examples)}", callback_data="ignore")
    builder.button(text="▶", callback_data=ExampleNav(action=f"next_{kind}", idx=idx))
    builder.button(
        text="🗑 Удалить этот", callback_data=ExampleNav(action=f"del_one_{kind}", idx=idx)
    )
    builder.button(text="← Назад", callback_data=ExampleNav(action="back_to_menu"))
    builder.adjust(3, 1, 1)

    await msg.edit_text(
        f"{emoji} Пример #{idx + 1}:\n\n{display_text}", reply_markup=builder.as_markup()
    )


@router.callback_query(ExampleNav.filter(F.action == "show_pos"))
@router.callback_query(ExampleNav.filter(F.action == "show_neg"))
async def callback_show_examples(
    callback: CallbackQuery, callback_data: ExampleNav, runner: "TenantRunner"
) -> None:
    kind: Literal["pos", "neg"] = "pos" if callback_data.action == "show_pos" else "neg"
    tenant_id, user_id, profile_id = await _get_tenant_and_profile(runner, callback.from_user.id)
    profile = await runner.get_candidate_profile(tenant_id, user_id, profile_id)

    if not profile or not profile.profile.search_profiles:
        await callback.answer("Нет примеров")
        return

    sp = profile.profile.search_profiles[0]
    examples = sp.positive_example_texts if kind == "pos" else sp.negative_example_texts

    if not examples:
        await callback.answer("Нет примеров этого типа")
        return

    await _show_example_page(callback, examples, 0, kind)


@router.callback_query(
    ExampleNav.filter(F.action.in_(["prev_pos", "next_pos", "prev_neg", "next_neg"]))
)
async def callback_nav_examples(
    callback: CallbackQuery, callback_data: ExampleNav, runner: "TenantRunner"
) -> None:
    kind: Literal["pos", "neg"] = "pos" if "pos" in callback_data.action else "neg"
    tenant_id, user_id, profile_id = await _get_tenant_and_profile(runner, callback.from_user.id)
    profile = await runner.get_candidate_profile(tenant_id, user_id, profile_id)
    if not profile or not profile.profile.search_profiles:
        return

    sp = profile.profile.search_profiles[0]
    examples = sp.positive_example_texts if kind == "pos" else sp.negative_example_texts

    new_idx = callback_data.idx
    if "prev" in callback_data.action:
        new_idx = (new_idx - 1) % len(examples)
    else:
        new_idx = (new_idx + 1) % len(examples)

    await _show_example_page(callback, examples, new_idx, kind)


@router.callback_query(ExampleNav.filter(F.action.in_(["del_one_pos", "del_one_neg"])))
async def callback_del_one(
    callback: CallbackQuery, callback_data: ExampleNav, runner: "TenantRunner"
) -> None:
    kind: Literal["pos", "neg"] = "pos" if "pos" in callback_data.action else "neg"
    ex_type = "positive_resume" if kind == "pos" else "negative_resume"
    tenant_id, user_id, profile_id = await _get_tenant_and_profile(runner, callback.from_user.id)
    profile = await runner.get_candidate_profile(tenant_id, user_id, profile_id)

    if not profile:
        await callback.answer("Ошибка: профиль не найден")
        return

    updated = remove_example_from_profile(profile, ex_type, callback_data.idx)

    runtime = runner.get_runtime(tenant_id)
    if runtime.embedding_provider:
        updated = await embed_profile_examples(updated, runtime.embedding_provider)

    await runner.save_candidate_profile(tenant_id, updated)
    await callback.answer("✅ Удалён")

    pos, neg = _get_example_counts(updated)
    sp = updated.profile.search_profiles[0]
    examples = sp.positive_example_texts if kind == "pos" else sp.negative_example_texts

    msg = callback.message
    if not isinstance(msg, Message):
        return

    if not examples:
        builder = InlineKeyboardBuilder()
        builder.button(text=f"📗 Позитивные ({pos})", callback_data=ExampleNav(action="show_pos"))
        builder.button(text=f"📕 Негативные ({neg})", callback_data=ExampleNav(action="show_neg"))
        builder.button(text="🗑 Удалить все", callback_data=ExampleNav(action="del_all"))
        builder.adjust(2, 1)
        await msg.edit_text("Ваши примеры:", reply_markup=builder.as_markup())
    else:
        new_idx = min(callback_data.idx, len(examples) - 1)
        await _show_example_page(callback, examples, new_idx, kind)


@router.callback_query(ExampleNav.filter(F.action == "back_to_menu"))
async def callback_back(callback: CallbackQuery, runner: "TenantRunner") -> None:
    tenant_id, user_id, profile_id = await _get_tenant_and_profile(runner, callback.from_user.id)
    profile = await runner.get_candidate_profile(tenant_id, user_id, profile_id)
    pos, neg = _get_example_counts(profile)

    builder = InlineKeyboardBuilder()
    builder.button(text=f"📗 Позитивные ({pos})", callback_data=ExampleNav(action="show_pos"))
    builder.button(text=f"📕 Негативные ({neg})", callback_data=ExampleNav(action="show_neg"))
    builder.button(text="🗑 Удалить все", callback_data=ExampleNav(action="del_all"))
    builder.adjust(2, 1)

    msg = callback.message
    if isinstance(msg, Message):
        await msg.edit_text("Ваши примеры:", reply_markup=builder.as_markup())


@router.callback_query(ExampleNav.filter(F.action == "del_all"))
async def callback_del_all(callback: CallbackQuery, runner: "TenantRunner") -> None:
    tenant_id, user_id, profile_id = await _get_tenant_and_profile(runner, callback.from_user.id)
    profile = await runner.get_candidate_profile(tenant_id, user_id, profile_id)
    pos, neg = _get_example_counts(profile)

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, удалить все", callback_data=ExampleNav(action="del_confirm"))
    builder.button(text="❌ Отмена", callback_data=ExampleNav(action="back_to_menu"))

    msg = callback.message
    if isinstance(msg, Message):
        await msg.edit_text(
            f"⚠️ Удалить ВСЕ примеры ({pos}+ и {neg}−)?", reply_markup=builder.as_markup()
        )


@router.callback_query(ExampleNav.filter(F.action == "del_confirm"))
async def callback_del_confirm(callback: CallbackQuery, runner: "TenantRunner") -> None:
    tenant_id, user_id, profile_id = await _get_tenant_and_profile(runner, callback.from_user.id)
    profile = await runner.get_candidate_profile(tenant_id, user_id, profile_id)

    if profile and profile.profile.search_profiles:
        sp = profile.profile.search_profiles[0]
        new_sp = sp.model_copy(
            update={
                "positive_example_texts": (),
                "negative_example_texts": (),
                "negative_embedding_vectors": (),
                "embedding_vector": None,
            }
        )
        new_candidate = profile.profile.model_copy(update={"search_profiles": (new_sp,)})
        updated = profile.model_copy(update={"profile": new_candidate})
        await runner.save_candidate_profile(tenant_id, updated)

    await callback.answer("✅ Все примеры удалены.")
    msg = callback.message
    if isinstance(msg, Message):
        await msg.edit_text("Примеров нет. /positive — добавить новые")


@router.callback_query(F.data == "ignore")
async def callback_ignore(callback: CallbackQuery) -> None:
    await callback.answer()


def create_router() -> Router:
    r = Router(name="examples")
    r.message.register(cmd_positive, Command("positive"))
    r.message.register(cmd_negative, Command("negative"))
    r.message.register(cmd_done, Command("done"), StateFilter(AddingExamples))
    r.message.register(handle_document_example, StateFilter(AddingExamples), F.document)
    r.message.register(
        handle_text_example, StateFilter(AddingExamples), F.text, ~F.text.startswith("/")
    )
    r.message.register(cmd_examples, Command("examples"))
    r.callback_query.register(
        callback_show_examples, ExampleNav.filter(F.action == "show_pos")
    )
    r.callback_query.register(
        callback_show_examples, ExampleNav.filter(F.action == "show_neg")
    )
    r.callback_query.register(
        callback_nav_examples,
        ExampleNav.filter(F.action.in_(["prev_pos", "next_pos", "prev_neg", "next_neg"])),
    )
    r.callback_query.register(
        callback_del_one, ExampleNav.filter(F.action.in_(["del_one_pos", "del_one_neg"]))
    )
    r.callback_query.register(callback_back, ExampleNav.filter(F.action == "back_to_menu"))
    r.callback_query.register(callback_del_all, ExampleNav.filter(F.action == "del_all"))
    r.callback_query.register(callback_del_confirm, ExampleNav.filter(F.action == "del_confirm"))
    r.callback_query.register(callback_ignore, F.data == "ignore")
    return r
