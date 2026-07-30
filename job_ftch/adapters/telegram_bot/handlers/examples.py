import asyncio
from io import BytesIO
from typing import TYPE_CHECKING, Any, Literal, cast

import structlog
from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from job_ftch.adapters.telegram_bot.fsm.states import AddingExamples, AddingJobExamples
from job_ftch.adapters.telegram_bot.utils import safe_error_reply
from job_ftch.application.profile_inputs import (
    build_profile_from_resume_text_async,
    embed_profile_examples,
    merge_resume_profile,
    remove_example_from_profile,
)
from job_ftch.application.profile_parsing import (
    TUNED_PROFILE_WEIGHTS,
    TUNED_RELEVANCE_THRESHOLD,
)
from job_ftch.domain import (
    CandidateIdentity,
    CandidateProfile,
    ManagedCandidateProfile,
    SearchProfile,
)
from job_ftch.infrastructure.document_parser import DocumentParseError, parse_document

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


def _default_search_profile() -> SearchProfile:
    return SearchProfile(
        weights=TUNED_PROFILE_WEIGHTS,
        relevance_threshold=TUNED_RELEVANCE_THRESHOLD,
    )


async def _get_tenant_and_profile(
    runner: "TenantRunner", from_user_id: int | None
) -> tuple[str, str, str]:
    user_id_str = str(from_user_id) if from_user_id is not None else "0"
    tenant_id = await runner.get_selected_tenant_id(user_id_str)
    profile_id = _profile_id(user_id_str)
    return tenant_id, user_id_str, profile_id


def _get_example_counts(profile: ManagedCandidateProfile | None) -> tuple[int, int]:
    if not profile or not profile.profile.search_profiles:
        return 0, 0
    sp = profile.profile.search_profiles[0]
    return len(sp.positive_example_texts), len(sp.negative_example_texts)


def _get_job_example_counts(profile: ManagedCandidateProfile | None) -> tuple[int, int]:
    if not profile or not profile.profile.search_profiles:
        return 0, 0
    sp = profile.profile.search_profiles[0]
    return len(sp.positive_job_example_texts), len(sp.negative_job_example_texts)


def _build_resumes_menu(pos: int, neg: int) -> InlineKeyboardBuilder:
    """Build the resume-only section menu: view pos / view neg / delete all resumes."""
    builder = InlineKeyboardBuilder()
    builder.button(text=f"📗 Подходящие ({pos})", callback_data=ExampleNav(action="show_pos"))
    builder.button(text=f"📕 Неподходящие ({neg})", callback_data=ExampleNav(action="show_neg"))
    builder.button(text="🗑 Удалить все резюме", callback_data=ExampleNav(action="del_all_resumes"))
    builder.adjust(2)
    return builder


def _build_vacancies_menu(pos_job: int, neg_job: int) -> InlineKeyboardBuilder:
    """Build the vacancy-only section menu: view pos / view neg / delete all vacancies."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"💼 Подходящие ({pos_job})", callback_data=ExampleNav(action="show_pos_job")
    )
    builder.button(
        text=f"📄 Неподходящие ({neg_job})", callback_data=ExampleNav(action="show_neg_job")
    )
    builder.button(
        text="🗑 Удалить все вакансии", callback_data=ExampleNav(action="del_all_vacancies")
    )
    builder.adjust(2)
    return builder


def _build_examples_launcher(
    pos: int, neg: int, pos_job: int, neg_job: int
) -> InlineKeyboardBuilder:
    """Build the launcher with two section buttons — resumes and vacancies."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"📋 Резюме ({pos}+ / {neg}−)", callback_data=ExampleNav(action="open_resumes")
    )
    builder.button(
        text=f"💼 Вакансии ({pos_job}+ / {neg_job}−)",
        callback_data=ExampleNav(action="open_vacancies"),
    )
    builder.adjust(1)
    return builder


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
        f"Кидай PDF или текст резюме. Отправь /done когда закончишь.\n\n"
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
        f"Кидай PDF или текст резюме которые НЕ подходят. /done когда закончишь.\n\n"
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
        f"/vacancies — посмотреть вакансии\n"
        f"/sources — настроить источники"
    )


@router.message(Command("done"), StateFilter(AddingJobExamples))
async def cmd_done_job(message: Message, state: FSMContext, runner: "TenantRunner") -> None:
    tenant_id, user_id, profile_id = await _get_tenant_and_profile(
        runner, message.from_user.id if message.from_user else None
    )
    profile = await runner.get_candidate_profile(tenant_id, user_id, profile_id)
    pos_j, neg_j = _get_job_example_counts(profile)
    await state.clear()
    await message.answer(
        f"✅ Готово. Вакансий: {pos_j}+ / {neg_j}−\n\n"
        f"/examples — посмотреть все примеры\n"
        f"/vacancies — посмотреть вакансии\n"
        f"/sources — настроить источники"
    )


@router.message(StateFilter(AddingJobExamples), F.document)
async def handle_job_document_example(
    message: Message, state: FSMContext, runner: "TenantRunner", bot: Bot
) -> None:
    document = message.document
    if not document:
        return

    tenant_id, user_id, profile_id = await _get_tenant_and_profile(
        runner, message.from_user.id if message.from_user else None
    )

    status_msg = await message.answer("⏳ Обработка документа вакансии...")
    sync_failed = False

    lock = _get_user_lock(user_id)
    async with lock:
        try:
            file = await bot.get_file(document.file_id)
            content = BytesIO()
            if file.file_path:
                await bot.download_file(file.file_path, content)

            raw_bytes = content.getvalue()
            if not raw_bytes:
                await status_msg.edit_text(
                    "⚠️ Файл пустой или слишком большой.\nСкопируй текст вакансии и пришли текстом."
                )
                return

            try:
                text = parse_document(raw_bytes, document.file_name or "vacancy.txt")
            except DocumentParseError:
                await status_msg.edit_text(
                    "❌ Не удалось прочитать файл. Попробуй вставить текст вакансии вручную."
                )
                return

            if len(text) < 30:
                await status_msg.edit_text(
                    "⚠️ Текст слишком короткий. Пришлите полный текст вакансии."
                )
                return

            existing = await runner.get_candidate_profile(tenant_id, user_id, profile_id)
            if not existing:
                existing = ManagedCandidateProfile(
                    user_id=user_id,
                    profile_id=profile_id,
                    profile=CandidateProfile(
                        identity=CandidateIdentity(candidate_id=user_id, display_name="User"),
                        search_profiles=(_default_search_profile(),),
                    ),
                )

            curr_state = await state.get_state()
            is_negative = curr_state == AddingJobExamples.negative.state
            kind = "negative_job" if is_negative else "positive_job"

            runtime = runner.get_runtime(tenant_id)
            if runtime.llm_provider is not None and runtime.ontology_store is not None:
                from job_ftch.application.ontology_enrichment import (
                    add_example_to_profile_with_enrichment,
                )

                managed = await add_example_to_profile_with_enrichment(
                    existing,
                    text,
                    kind=kind,
                    llm=runtime.llm_provider,
                    ontology_store=cast("Any", runtime.ontology_store),
                )
            else:
                from job_ftch.application.resume_extraction import add_example_to_profile

                managed = add_example_to_profile(existing, text, kind=kind)

            if runtime.embedding_provider:
                from job_ftch.application.profile_inputs import embed_profile_examples

                managed = await embed_profile_examples(managed, runtime.embedding_provider)

            await runner.save_and_activate_candidate_profile(tenant_id, managed)
            from job_ftch.application.shot_sync import sync_profile_to_shot_store

            try:
                await sync_profile_to_shot_store(
                    profile=managed,
                    tenant_id=tenant_id,
                    user_id=user_id,
                )
            except Exception as sync_exc:  # noqa: BLE001 - non-fatal
                sync_failed = True
                logger.warning(
                    "shot_store_sync_failed",
                    tenant_id=tenant_id,
                    user_id=user_id,
                    error=str(sync_exc),
                )

            sp = managed.profile.search_profiles[0]
            count = (
                len(sp.negative_job_example_texts)
                if is_negative
                else len(sp.positive_job_example_texts)
            )

        except Exception as e:
            await safe_error_reply(message, e, "failed_to_process_job_document")
            return

    preview = (text[:300] + "...") if len(text) > 300 else text
    if sync_failed:
        await status_msg.edit_text(
            f"⚠️ Вакансия #{count} добавлена локально (шоты не сохранены в векторную БД).\n\n"
            f"📋 Preview:\n{preview}\n\n"
            f"/done — закончить /vacancies — посмотреть все"
        )
    else:
        await status_msg.edit_text(
            f"✅ Вакансия #{count} добавлена.\n\n"
            f"📋 Preview:\n{preview}\n\n"
            f"/done — закончить /vacancies — посмотреть все"
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
    sync_failed = False

    lock = _get_user_lock(user_id)
    async with lock:
        try:
            file = await bot.get_file(document.file_id)
            content = BytesIO()
            if file.file_path:
                await bot.download_file(file.file_path, content)

            raw_bytes = content.getvalue()
            if not raw_bytes:
                await status_msg.edit_text(
                    "⚠️ Файл пустой или слишком большой для скачивания (>20MB).\n"
                    "Скопируй текст резюме и пришли его текстом."
                )
                return

            try:
                text = parse_document(raw_bytes, document.file_name or "resume.txt")
            except DocumentParseError:
                await status_msg.edit_text(
                    "❌ Не удалось прочитать файл. Попробуй вставить текст резюме вручную."
                )
                return

            if not text or len(text.strip()) < 30:
                await status_msg.edit_text(
                    "❌ Не удалось прочитать файл. Попробуй вставить текст резюме вручную."
                )
                return

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

            if runtime.llm_provider is not None and runtime.ontology_store is not None:
                from job_ftch.application.ontology_enrichment import _enrich_ontology_from_shot

                try:
                    await _enrich_ontology_from_shot(
                        text,
                        kind="negative_resume" if is_negative else "positive_resume",
                        llm=runtime.llm_provider,
                        ontology_store=cast("Any", runtime.ontology_store),
                    )
                except Exception as enrich_exc:  # noqa: BLE001 - non-fatal parity fix
                    logger.warning(
                        "resume_document_enrichment_failed",
                        tenant_id=tenant_id,
                        user_id=user_id,
                        error=str(enrich_exc),
                    )

            # Save + activate in one step. Two-call patterns race on
            # the first /run (see TenantStore.save_and_activate_*).
            await runner.save_and_activate_candidate_profile(tenant_id, managed)

            # Persist the document-derived resume text into BOTH
            # the in-memory BGE-M3 registry and the Qdrant
            # collection (default backend). The bulk helper
            # re-encodes every shot bucket (positive/negative,
            # resume/vacancy) so any change to the parsed PDF
            # is reflected in the relevance store on the very
            # next /run.
            from job_ftch.application.shot_sync import sync_profile_to_shot_store

            try:
                await sync_profile_to_shot_store(
                    profile=managed,
                    tenant_id=tenant_id,
                    user_id=user_id,
                )
            except Exception as sync_exc:  # noqa: BLE001 - non-fatal
                sync_failed = True
                logger.warning(
                    "shot_store_sync_failed",
                    tenant_id=tenant_id,
                    user_id=user_id,
                    error=str(sync_exc),
                )

            pos, neg = _get_example_counts(managed)
            count = neg if is_negative else pos

            # Show roles detected from THIS resume, not the accumulated
            # union across all uploads — a [:3] slice of the merged
            # profile freezes after the third distinct role and makes
            # every later upload look like it produced the same profile.
            roles_preview = "не определено"
            if extracted.profile.search_profiles:
                extracted_roles = extracted.profile.search_profiles[0].target_roles
                if extracted_roles:
                    roles_preview = ", ".join(extracted_roles[:3])
                    if len(extracted_roles) > 3:
                        roles_preview += f" (+{len(extracted_roles) - 3})"

        except Exception as e:
            await safe_error_reply(message, e, "failed_to_process_document")
            return

    # Reply outside the lock
    if sync_failed:
        await status_msg.edit_text(
            f"⚠️ #{count} добавлен локально (шоты не сохранены в векторную БД). Ролей: {roles_preview}. /done чтобы закончить."
        )
    else:
        await status_msg.edit_text(
            f"✅ #{count} добавлен. Ролей: {roles_preview}. /done чтобы закончить."
        )


@router.message(StateFilter(AddingExamples), F.text, ~F.text.startswith("/"))
async def handle_text_example(message: Message, state: FSMContext, runner: "TenantRunner") -> None:
    text = ((message.text or message.caption) or "").strip()
    if len(text) < 30:
        await message.answer("Текст слишком короткий, кинь нормальное описание.")
        return

    curr_state = await state.get_state()
    is_negative = curr_state == AddingExamples.negative.state

    tenant_id, user_id, profile_id = await _get_tenant_and_profile(
        runner, message.from_user.id if message.from_user else None
    )

    lock = _get_user_lock(user_id)
    sync_failed = False
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
                        search_profiles=(_default_search_profile(),),
                    ),
                )

            runtime = runner.get_runtime(tenant_id)
            kind = "negative_resume" if is_negative else "positive_resume"
            if runtime.llm_provider is not None and runtime.ontology_store is not None:
                from job_ftch.application.ontology_enrichment import (
                    add_example_to_profile_with_enrichment,
                )

                managed = await add_example_to_profile_with_enrichment(
                    existing,
                    text,
                    kind=kind,
                    llm=runtime.llm_provider,
                    ontology_store=cast("Any", runtime.ontology_store),
                )
            else:
                from job_ftch.application.resume_extraction import add_example_to_profile

                managed = add_example_to_profile(existing, text, kind=kind)

            if runtime.embedding_provider:
                managed = await embed_profile_examples(managed, runtime.embedding_provider)

            # Save + activate in one step (BR-FIX-RACE).
            await runner.save_and_activate_candidate_profile(tenant_id, managed)

            # Persist the shot to BOTH the in-memory registry and
            # the Qdrant collection (default backend). The
            # ``add_shot`` helper encodes once and writes to both
            # stores with a deterministic point id, so a re-add of
            # the same text overwrites the existing point instead
            # of accumulating duplicates.
            from job_ftch.application.shot_sync import (
                add_shot_async,
                remove_shot_async,
            )

            try:
                # Clear any prior copy of this exact text from both
                # stores first (idempotent re-encoding). The role
                # used here is the same role the user just wrote
                # the example to: either resume:{label} or
                # vacancy:{label}.
                bucket = "negative" if is_negative else "positive"
                await remove_shot_async(
                    text=text, role=f"user:{user_id}@tenant:{tenant_id}:resume:{bucket}"
                )
                await add_shot_async(
                    text=text,
                    label=bucket,
                    role=f"user:{user_id}@tenant:{tenant_id}:resume:{bucket}",
                    tenant_id=tenant_id,
                    user_id=user_id,
                )
            except Exception as sync_exc:  # noqa: BLE001 - non-fatal
                sync_failed = True
                logger.warning(
                    "shot_store_sync_failed",
                    tenant_id=tenant_id,
                    user_id=user_id,
                    error=str(sync_exc),
                )

            pos, neg = _get_example_counts(managed)
            count = neg if is_negative else pos

        except Exception as e:
            await safe_error_reply(message, e, "failed_to_process_text")
            return

    # Reply outside the lock
    if sync_failed:
        await message.answer(
            f"⚠️ Текстовый пример #{count} добавлен локально (шоты не сохранены в векторную БД)."
        )
    else:
        await message.answer(f"✅ Текстовый пример #{count} добавлен.")


@router.message(StateFilter(AddingExamples), F.photo)
async def handle_photo_example(message: Message, state: FSMContext, runner: "TenantRunner") -> None:
    if not (message.caption or "").strip():
        await message.answer("К фото нужен caption с текстом резюме/примера.")
        return
    await handle_text_example(message, state, runner)


@router.message(Command("positive_job"))
async def cmd_positive_job(
    message: Message, state: FSMContext, runner: "TenantRunner", user_id_override: int | None = None
) -> None:
    await state.set_state(AddingJobExamples.positive)
    resolved_uid = (
        user_id_override
        if user_id_override is not None
        else (message.from_user.id if message.from_user else None)
    )
    tenant_id, user_id, profile_id = await _get_tenant_and_profile(runner, resolved_uid)
    profile = await runner.get_candidate_profile(tenant_id, user_id, profile_id)
    if not profile or not profile.profile.search_profiles:
        pos_j, neg_j = 0, 0
    else:
        sp = profile.profile.search_profiles[0]
        pos_j = len(sp.positive_job_example_texts)
        neg_j = len(sp.negative_job_example_texts)
    await message.answer(
        f"Кидай текст ПОДХОДЯЩЕЙ вакансии (copy-paste из объявления). /done когда закончишь.\n\n"
        f"Текущий счёт: {pos_j}+ / {neg_j}−"
    )


@router.message(Command("negative_job"))
async def cmd_negative_job(
    message: Message, state: FSMContext, runner: "TenantRunner", user_id_override: int | None = None
) -> None:
    await state.set_state(AddingJobExamples.negative)
    resolved_uid = (
        user_id_override
        if user_id_override is not None
        else (message.from_user.id if message.from_user else None)
    )
    tenant_id, user_id, profile_id = await _get_tenant_and_profile(runner, resolved_uid)
    profile = await runner.get_candidate_profile(tenant_id, user_id, profile_id)
    if not profile or not profile.profile.search_profiles:
        pos_j, neg_j = 0, 0
    else:
        sp = profile.profile.search_profiles[0]
        pos_j = len(sp.positive_job_example_texts)
        neg_j = len(sp.negative_job_example_texts)
    await message.answer(
        f"Кидай текст НЕ подходящей вакансии. /done когда закончишь.\n\n"
        f"Текущий счёт: {pos_j}+ / {neg_j}−"
    )


@router.message(StateFilter(AddingJobExamples), F.text, ~F.text.startswith("/"))
async def handle_job_text_example(
    message: Message, state: FSMContext, runner: "TenantRunner"
) -> None:
    text = ((message.text or message.caption) or "").strip()
    if len(text) < 30:
        await message.answer("Текст слишком короткий. Скопируй полный текст вакансии.")
        return

    curr_state = await state.get_state()
    is_negative = curr_state == AddingJobExamples.negative.state

    tenant_id, user_id, profile_id = await _get_tenant_and_profile(
        runner, message.from_user.id if message.from_user else None
    )

    lock = _get_user_lock(user_id)
    sync_failed = False
    async with lock:
        try:
            existing = await runner.get_candidate_profile(tenant_id, user_id, profile_id)
            if not existing:
                existing = ManagedCandidateProfile(
                    user_id=user_id,
                    profile_id=profile_id,
                    profile=CandidateProfile(
                        identity=CandidateIdentity(candidate_id=user_id, display_name="User"),
                        search_profiles=(_default_search_profile(),),
                    ),
                )

            runtime = runner.get_runtime(tenant_id)
            kind = "negative_job" if is_negative else "positive_job"
            if runtime.llm_provider is not None and runtime.ontology_store is not None:
                from job_ftch.application.ontology_enrichment import (
                    add_example_to_profile_with_enrichment,
                )

                managed = await add_example_to_profile_with_enrichment(
                    existing,
                    text,
                    kind=kind,
                    llm=runtime.llm_provider,
                    ontology_store=cast("Any", runtime.ontology_store),
                )
            else:
                from job_ftch.application.resume_extraction import add_example_to_profile

                managed = add_example_to_profile(existing, text, kind=kind)

            if runtime.embedding_provider:
                managed = await embed_profile_examples(managed, runtime.embedding_provider)

            # Save + activate in one step.
            await runner.save_and_activate_candidate_profile(tenant_id, managed)

            # Push the new job example into BOTH the in-memory
            # registry and the Qdrant collection (default
            # backend). The ``add_shot`` helper encodes once and
            # writes to both stores with a deterministic point
            # id, so a re-add of the same text overwrites the
            # existing point instead of accumulating
            # duplicates.
            from job_ftch.application.shot_sync import (
                add_shot_async,
                remove_shot_async,
            )

            try:
                bucket = "negative" if is_negative else "positive"
                full_role = f"user:{user_id}@tenant:{tenant_id}:vacancy:{bucket}"
                await remove_shot_async(text=text, role=full_role)
                await add_shot_async(
                    text=text,
                    label=bucket,
                    role=full_role,
                    tenant_id=tenant_id,
                    user_id=user_id,
                )
            except Exception as sync_exc:  # noqa: BLE001 - non-fatal
                sync_failed = True
                logger.warning(
                    "shot_store_sync_failed",
                    tenant_id=tenant_id,
                    user_id=user_id,
                    error=str(sync_exc),
                )

            sp = managed.profile.search_profiles[0]
            count = (
                len(sp.negative_job_example_texts)
                if is_negative
                else len(sp.positive_job_example_texts)
            )
            preview = (text[:300] + "...") if len(text) > 300 else text
            reply_text = (
                f"⚠️ Вакансия #{count} добавлена локально (шоты не сохранены в векторную БД).\n\n"
                f"📋 Preview:\n{preview}\n\n"
                f"/vacancies — посмотреть все\n"
                f"/done — закончить"
                if sync_failed
                else f"✅ Вакансия #{count} добавлена.\n\n"
                f"📋 Preview:\n{preview}\n\n"
                f"/vacancies — посмотреть все\n"
                f"/done — закончить"
            )
            await message.answer(reply_text)

        except Exception as e:
            await safe_error_reply(message, e, "failed_to_process_job_text")
            return


@router.message(StateFilter(AddingJobExamples), F.photo)
async def handle_job_photo_example(
    message: Message, state: FSMContext, runner: "TenantRunner"
) -> None:
    if not (message.caption or "").strip():
        await message.answer("К фото нужен caption с полным текстом вакансии.")
        return
    await handle_job_text_example(message, state, runner)


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
    pos_job, neg_job = _get_job_example_counts(profile)

    if pos == 0 and neg == 0 and pos_job == 0 and neg_job == 0:
        await message.answer(
            "Примеров ещё нет.\n\n"
            "/positive — добавить подходящее резюме\n"
            "/negative — добавить неподходящее резюме\n"
            "/positive_job — добавить подходящую вакансию\n"
            "/negative_job — добавить неподходящую вакансию"
        )
        return

    builder = _build_examples_launcher(pos, neg, pos_job, neg_job)
    # Counts belong in the text: a bare header reads as a truncated reply when
    # the client collapses or fails to render the inline keyboard.
    await message.answer(
        f"🗂 Ваши примеры:\n📗 Резюме: {pos}+ / {neg}−\n💼 Вакансии: {pos_job}+ / {neg_job}−",
        reply_markup=builder.as_markup(),
    )


@router.message(Command("resumes"))
async def cmd_resumes(
    message: Message, runner: "TenantRunner", user_id_override: int | None = None
) -> None:
    """Open the resume section menu directly (separate from vacancies)."""
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
            "Резюме ещё нет.\n\n"
            "/positive — добавить подходящее резюме\n"
            "/negative — добавить неподходящее резюме"
        )
        return

    builder = _build_resumes_menu(pos, neg)
    await message.answer(
        f"📗 Резюме ({pos}+ / {neg}−)\n\n"
        f"/positive — добавить подходящее\n"
        f"/negative — добавить неподходящее",
        reply_markup=builder.as_markup(),
    )


@router.message(Command("vacancies"))
async def cmd_vacancies(
    message: Message, runner: "TenantRunner", user_id_override: int | None = None
) -> None:
    """Open the vacancy section menu directly (separate from resumes)."""
    resolved_uid = (
        user_id_override
        if user_id_override is not None
        else (message.from_user.id if message.from_user else None)
    )
    tenant_id, user_id, profile_id = await _get_tenant_and_profile(runner, resolved_uid)
    profile = await runner.get_candidate_profile(tenant_id, user_id, profile_id)
    pos_job, neg_job = _get_job_example_counts(profile)

    if pos_job == 0 and neg_job == 0:
        await message.answer(
            "Вакансий ещё нет.\n\n"
            "/positive_job — добавить подходящую вакансию\n"
            "/negative_job — добавить неподходящую вакансию"
        )
        return

    builder = _build_vacancies_menu(pos_job, neg_job)
    await message.answer(
        f"💼 Вакансии ({pos_job}+ / {neg_job}−)\n\n"
        f"/positive_job — добавить подходящую\n"
        f"/negative_job — добавить неподходящую",
        reply_markup=builder.as_markup(),
    )


async def _show_example_page(
    callback: CallbackQuery,
    examples: tuple[str, ...],
    idx: int,
    kind: Literal["pos", "neg", "pos_job", "neg_job"],
) -> None:
    msg = callback.message
    if not isinstance(msg, Message):
        return

    text = examples[idx]
    display_text = text[:800] + ("..." if len(text) > 800 else "")
    _emoji_map = {"pos": "📗", "neg": "📕", "pos_job": "💼", "neg_job": "📄"}
    emoji = _emoji_map[kind]
    _label_map = {
        "pos": "Резюме+",
        "neg": "Резюме−",
        "pos_job": "Вакансия+",
        "neg_job": "Вакансия−",
    }
    label = _label_map[kind]

    back_action = "open_resumes" if kind in ("pos", "neg") else "open_vacancies"

    builder = InlineKeyboardBuilder()
    builder.button(text="◀", callback_data=ExampleNav(action=f"prev_{kind}", idx=idx))
    builder.button(text=f"{idx + 1} / {len(examples)}", callback_data="ignore")
    builder.button(text="▶", callback_data=ExampleNav(action=f"next_{kind}", idx=idx))
    builder.button(
        text="🗑 Удалить этот", callback_data=ExampleNav(action=f"del_one_{kind}", idx=idx)
    )
    builder.button(text="← Назад", callback_data=ExampleNav(action=back_action))
    builder.adjust(3, 1, 1)

    await msg.edit_text(
        f"{emoji} {label} #{idx + 1}:\n\n{display_text}", reply_markup=builder.as_markup()
    )


@router.callback_query(
    ExampleNav.filter(F.action.in_(["show_pos", "show_neg", "show_pos_job", "show_neg_job"]))
)
async def callback_show_examples(
    callback: CallbackQuery, callback_data: ExampleNav, runner: "TenantRunner"
) -> None:
    tenant_id, user_id, profile_id = await _get_tenant_and_profile(runner, callback.from_user.id)
    profile = await runner.get_candidate_profile(tenant_id, user_id, profile_id)

    if not profile or not profile.profile.search_profiles:
        await callback.answer("Нет примеров")
        return

    sp = profile.profile.search_profiles[0]
    action = callback_data.action
    if action == "show_pos":
        kind: Literal["pos", "neg", "pos_job", "neg_job"] = "pos"
        examples = sp.positive_example_texts
    elif action == "show_neg":
        kind = "neg"
        examples = sp.negative_example_texts
    elif action == "show_pos_job":
        kind = "pos_job"
        examples = sp.positive_job_example_texts
    else:
        kind = "neg_job"
        examples = sp.negative_job_example_texts

    if not examples:
        await callback.answer("Нет примеров этого типа")
        return

    await _show_example_page(callback, examples, 0, kind)


@router.callback_query(
    ExampleNav.filter(
        F.action.in_(
            [
                "prev_pos",
                "next_pos",
                "prev_neg",
                "next_neg",
                "prev_pos_job",
                "next_pos_job",
                "prev_neg_job",
                "next_neg_job",
            ]
        )
    )
)
async def callback_nav_examples(
    callback: CallbackQuery, callback_data: ExampleNav, runner: "TenantRunner"
) -> None:
    # Answered up front: this handler has two early returns and its renderer never answers
    # either, so every prev/next press used to leave the client spinning until it timed out.
    await callback.answer()
    action = callback_data.action
    tenant_id, user_id, profile_id = await _get_tenant_and_profile(runner, callback.from_user.id)
    profile = await runner.get_candidate_profile(tenant_id, user_id, profile_id)
    if not profile or not profile.profile.search_profiles:
        return

    sp = profile.profile.search_profiles[0]
    # Determine kind from action suffix
    for k in ("pos_job", "neg_job", "pos", "neg"):
        if action.endswith(k):
            kind: Literal["pos", "neg", "pos_job", "neg_job"] = k
            break
    else:
        return

    examples_map = {
        "pos": sp.positive_example_texts,
        "neg": sp.negative_example_texts,
        "pos_job": sp.positive_job_example_texts,
        "neg_job": sp.negative_job_example_texts,
    }
    examples = examples_map[kind]

    new_idx = callback_data.idx
    if action.startswith("prev"):
        new_idx = (new_idx - 1) % len(examples)
    else:
        new_idx = (new_idx + 1) % len(examples)

    await _show_example_page(callback, examples, new_idx, kind)


@router.callback_query(
    ExampleNav.filter(
        F.action.in_(["del_one_pos", "del_one_neg", "del_one_pos_job", "del_one_neg_job"])
    )
)
async def callback_del_one(
    callback: CallbackQuery, callback_data: ExampleNav, runner: "TenantRunner"
) -> None:
    action_map = {
        "del_one_pos": ("positive_resume", "pos"),
        "del_one_neg": ("negative_resume", "neg"),
        "del_one_pos_job": ("positive_job", "pos_job"),
        "del_one_neg_job": ("negative_job", "neg_job"),
    }
    remove_kind, display_kind = action_map[callback_data.action]
    kind: Literal["pos", "neg", "pos_job", "neg_job"] = display_kind  # type: ignore

    tenant_id, user_id, profile_id = await _get_tenant_and_profile(runner, callback.from_user.id)
    profile = await runner.get_candidate_profile(tenant_id, user_id, profile_id)

    if not profile:
        await callback.answer("Ошибка: профиль не найден")
        return

    # Capture the text we are about to remove so we can also drop
    # its encoding from the in-memory BGE-M3 store.
    removed_text: str | None = None
    if profile.profile.search_profiles:
        sp_new = profile.profile.search_profiles[0]
        bucket = {
            "positive_resume": sp_new.positive_example_texts,
            "negative_resume": sp_new.negative_example_texts,
            "positive_job": sp_new.positive_job_example_texts,
            "negative_job": sp_new.negative_job_example_texts,
        }[remove_kind]
        if 0 <= callback_data.idx < len(bucket):
            removed_text = bucket[callback_data.idx]

    updated = remove_example_from_profile(profile, remove_kind, callback_data.idx)

    runtime = runner.get_runtime(tenant_id)
    if runtime.embedding_provider:
        updated = await embed_profile_examples(updated, runtime.embedding_provider)

    await runner.save_and_activate_candidate_profile(tenant_id, updated)

    # Drop the deleted text from the BGE-M3 store so the relevance
    # scorer at the next /run no longer matches against it. We
    # know the role (e.g. ``vacancy:positive``) from the action, so
    # the Qdrant point id is deterministic and a re-add of the
    # same text later will reuse the slot.
    shot_remove_failed = False
    if removed_text:
        try:
            from job_ftch.application.shot_sync import remove_shot_async

            bucket_role = {
                "positive_resume": "resume:positive",
                "negative_resume": "resume:negative",
                "positive_job": "vacancy:positive",
                "negative_job": "vacancy:negative",
            }[remove_kind]
            full_role = f"user:{user_id}@tenant:{tenant_id}:{bucket_role}"
            await remove_shot_async(text=removed_text, role=full_role)
        except Exception as sync_exc:  # noqa: BLE001 - reported to user below
            shot_remove_failed = True
            logger.warning(
                "shot_store_remove_failed",
                tenant_id=tenant_id,
                user_id=user_id,
                error=str(sync_exc),
            )

    if shot_remove_failed:
        # The profile (Postgres) is already updated, but the vector
        # store may still hold the deleted example — it can keep
        # affecting relevance scoring until removed by hand. Telling
        # the user beats a silent inconsistency.
        await callback.answer("⚠️ Удалён из профиля, но не из векторной базы", show_alert=True)
    else:
        await callback.answer("✅ Удалён")

    pos, neg = _get_example_counts(updated)
    pos_job, neg_job = _get_job_example_counts(updated)
    sp = updated.profile.search_profiles[0]
    examples_map = {
        "pos": sp.positive_example_texts,
        "neg": sp.negative_example_texts,
        "pos_job": sp.positive_job_example_texts,
        "neg_job": sp.negative_job_example_texts,
    }
    examples = examples_map[kind]

    msg = callback.message
    if not isinstance(msg, Message):
        return

    if not examples:
        if kind in ("pos", "neg"):
            builder = _build_resumes_menu(pos, neg)
            await msg.edit_text(f"📗 Резюме ({pos}+ / {neg}−)", reply_markup=builder.as_markup())
        else:
            builder = _build_vacancies_menu(pos_job, neg_job)
            await msg.edit_text(
                f"💼 Вакансии ({pos_job}+ / {neg_job}−)",
                reply_markup=builder.as_markup(),
            )
    else:
        new_idx = min(callback_data.idx, len(examples) - 1)
        await _show_example_page(callback, examples, new_idx, kind)


@router.callback_query(ExampleNav.filter(F.action == "open_resumes"))
async def callback_open_resumes(callback: CallbackQuery, runner: "TenantRunner") -> None:
    """Open the resume section menu (view pos / view neg / del all resumes)."""
    tenant_id, user_id, profile_id = await _get_tenant_and_profile(runner, callback.from_user.id)
    profile = await runner.get_candidate_profile(tenant_id, user_id, profile_id)
    pos, neg = _get_example_counts(profile)

    builder = _build_resumes_menu(pos, neg)
    msg = callback.message
    if isinstance(msg, Message):
        await msg.edit_text(
            f"📗 Резюме ({pos}+ / {neg}−)\n\n"
            f"/positive — добавить подходящее\n"
            f"/negative — добавить неподходящее",
            reply_markup=builder.as_markup(),
        )
    await callback.answer()


@router.callback_query(ExampleNav.filter(F.action == "open_vacancies"))
async def callback_open_vacancies(callback: CallbackQuery, runner: "TenantRunner") -> None:
    """Open the vacancy section menu (view pos / view neg / del all vacancies)."""
    tenant_id, user_id, profile_id = await _get_tenant_and_profile(runner, callback.from_user.id)
    profile = await runner.get_candidate_profile(tenant_id, user_id, profile_id)
    pos_job, neg_job = _get_job_example_counts(profile)

    builder = _build_vacancies_menu(pos_job, neg_job)
    msg = callback.message
    if isinstance(msg, Message):
        await msg.edit_text(
            f"💼 Вакансии ({pos_job}+ / {neg_job}−)\n\n"
            f"/positive_job — добавить подходящую\n"
            f"/negative_job — добавить неподходящую",
            reply_markup=builder.as_markup(),
        )
    await callback.answer()


@router.callback_query(ExampleNav.filter(F.action == "del_all_resumes"))
async def callback_del_all_resumes(callback: CallbackQuery, runner: "TenantRunner") -> None:
    """Confirm deletion of all resume shots only (vacancy shots stay intact)."""
    tenant_id, user_id, profile_id = await _get_tenant_and_profile(runner, callback.from_user.id)
    profile = await runner.get_candidate_profile(tenant_id, user_id, profile_id)
    pos, neg = _get_example_counts(profile)

    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Да, удалить резюме", callback_data=ExampleNav(action="del_confirm_resumes")
    )
    builder.button(text="❌ Отмена", callback_data=ExampleNav(action="open_resumes"))

    msg = callback.message
    if isinstance(msg, Message):
        await msg.edit_text(
            f"⚠️ Удалить ВСЕ резюме ({pos}+ / {neg}−)?\nВакансии не будут затронуты.",
            reply_markup=builder.as_markup(),
        )
    await callback.answer()


@router.callback_query(ExampleNav.filter(F.action == "del_all_vacancies"))
async def callback_del_all_vacancies(callback: CallbackQuery, runner: "TenantRunner") -> None:
    """Confirm deletion of all vacancy shots only (resume shots stay intact)."""
    tenant_id, user_id, profile_id = await _get_tenant_and_profile(runner, callback.from_user.id)
    profile = await runner.get_candidate_profile(tenant_id, user_id, profile_id)
    pos_job, neg_job = _get_job_example_counts(profile)

    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Да, удалить вакансии", callback_data=ExampleNav(action="del_confirm_vacancies")
    )
    builder.button(text="❌ Отмена", callback_data=ExampleNav(action="open_vacancies"))

    msg = callback.message
    if isinstance(msg, Message):
        await msg.edit_text(
            f"⚠️ Удалить ВСЕ вакансии ({pos_job}+ / {neg_job}−)?\nРезюме не будут затронуты.",
            reply_markup=builder.as_markup(),
        )
    await callback.answer()


@router.callback_query(ExampleNav.filter(F.action == "del_confirm_resumes"))
async def callback_del_confirm_resumes(callback: CallbackQuery, runner: "TenantRunner") -> None:
    """Delete all resume shots only (vacancy shots stay intact)."""
    from job_ftch.application.shot_sync import (
        remove_user_shots_async,
        sync_profile_to_shot_store,
    )

    tenant_id, user_id, profile_id = await _get_tenant_and_profile(runner, callback.from_user.id)
    profile = await runner.get_candidate_profile(tenant_id, user_id, profile_id)

    if profile and profile.profile.search_profiles:
        sp = profile.profile.search_profiles[0]
        new_sp = sp.model_copy(
            update={
                "positive_example_texts": (),
                "negative_example_texts": (),
            }
        )
        new_candidate = profile.profile.model_copy(update={"search_profiles": (new_sp,)})
        updated = profile.model_copy(update={"profile": new_candidate})
        runtime = runner.get_runtime(tenant_id)
        if runtime.embedding_provider:
            updated = await embed_profile_examples(updated, runtime.embedding_provider)
        await runner.save_and_activate_candidate_profile(tenant_id, updated)
        # Drop the resume shots from the BGE-M3 store too, but
        # keep the vacancy shots. ``remove_user_shots`` clears all
        # of the user's shots so we re-add the vacancies below.
        clear_failed = False
        try:
            await remove_user_shots_async(tenant_id=tenant_id, user_id=user_id)
        except Exception as clear_exc:  # noqa: BLE001 - reported to user below
            clear_failed = True
            logger.warning(
                "shot_store_user_clear_failed",
                tenant_id=tenant_id,
                user_id=user_id,
                error=str(clear_exc),
            )
        try:
            await sync_profile_to_shot_store(
                profile=updated,
                tenant_id=tenant_id,
                user_id=user_id,
            )
        except Exception as sync_exc:  # noqa: BLE001 - non-fatal
            logger.warning(
                "shot_store_sync_failed",
                tenant_id=tenant_id,
                user_id=user_id,
                error=str(sync_exc),
            )

        if clear_failed:
            await callback.answer(
                "⚠️ Резюме удалены из профиля, но векторная база могла не"
                " очиститься полностью — старые примеры могут ещё влиять на"
                " релевантность",
                show_alert=True,
            )
        else:
            await callback.answer("✅ Резюме удалены.")
    else:
        await callback.answer("✅ Резюме удалены.")
    msg = callback.message
    if isinstance(msg, Message):
        await msg.edit_text(
            "Резюме удалены.\n\n"
            "/positive — добавить подходящее резюме\n"
            "/negative — добавить неподходящее резюме\n\n"
            "/vacancies — посмотреть вакансии"
        )


@router.callback_query(ExampleNav.filter(F.action == "del_confirm_vacancies"))
async def callback_del_confirm_vacancies(callback: CallbackQuery, runner: "TenantRunner") -> None:
    """Delete all vacancy shots only (resume shots stay intact)."""
    from job_ftch.application.shot_sync import (
        remove_user_shots_async,
        sync_profile_to_shot_store,
    )

    tenant_id, user_id, profile_id = await _get_tenant_and_profile(runner, callback.from_user.id)
    profile = await runner.get_candidate_profile(tenant_id, user_id, profile_id)

    if profile and profile.profile.search_profiles:
        sp = profile.profile.search_profiles[0]
        new_sp = sp.model_copy(
            update={
                "positive_job_example_texts": (),
                "negative_job_example_texts": (),
            }
        )
        new_candidate = profile.profile.model_copy(update={"search_profiles": (new_sp,)})
        updated = profile.model_copy(update={"profile": new_candidate})
        runtime = runner.get_runtime(tenant_id)
        if runtime.embedding_provider:
            updated = await embed_profile_examples(updated, runtime.embedding_provider)
        await runner.save_and_activate_candidate_profile(tenant_id, updated)
        # Drop the vacancy shots from the BGE-M3 store but keep
        # the resume shots; the same re-add-after-clear trick
        # preserves the resume encoding while clearing vacancies.
        clear_failed = False
        try:
            await remove_user_shots_async(tenant_id=tenant_id, user_id=user_id)
        except Exception as clear_exc:  # noqa: BLE001 - reported to user below
            clear_failed = True
            logger.warning(
                "shot_store_user_clear_failed",
                tenant_id=tenant_id,
                user_id=user_id,
                error=str(clear_exc),
            )
        try:
            await sync_profile_to_shot_store(
                profile=updated,
                tenant_id=tenant_id,
                user_id=user_id,
            )
        except Exception as sync_exc:  # noqa: BLE001 - non-fatal
            logger.warning(
                "shot_store_sync_failed",
                tenant_id=tenant_id,
                user_id=user_id,
                error=str(sync_exc),
            )

        if clear_failed:
            await callback.answer(
                "⚠️ Вакансии удалены из профиля, но векторная база могла не"
                " очиститься полностью — старые примеры могут ещё влиять на"
                " релевантность",
                show_alert=True,
            )
        else:
            await callback.answer("✅ Вакансии удалены.")
    else:
        await callback.answer("✅ Вакансии удалены.")
    msg = callback.message
    if isinstance(msg, Message):
        await msg.edit_text(
            "Вакансии удалены.\n\n"
            "/positive_job — добавить подходящую вакансию\n"
            "/negative_job — добавить неподходящую вакансию\n\n"
            "/resumes — посмотреть резюме"
        )


@router.callback_query(F.data == "ignore")
async def callback_ignore(callback: CallbackQuery) -> None:
    await callback.answer()
