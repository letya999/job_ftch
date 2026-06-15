"""Profile handlers for the Telegram bot."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram import Router
from aiogram.filters import Command

from job_ftch.application.profile_inputs import (
    embed_profile_examples,
    remove_example_from_profile,
)

if TYPE_CHECKING:
    from aiogram.types import Message

    from job_ftch.application.contracts import EmbeddingProvider
    from job_ftch.application.tenant_runner import TenantRunner

router = Router(name="profiles")


def _profile_id(user_id: str) -> str:
    return f"user_{user_id}"


@router.message(Command("profiles"))
async def cmd_profiles(message: Message, runner: TenantRunner) -> None:
    tenant_ids = runner.tenant_ids()
    tenant_id = tenant_ids[0] if tenant_ids else "default"
    user_id_str = str(message.from_user.id if message.from_user else 0)
    profile_id = _profile_id(user_id_str)

    profile = await runner.get_candidate_profile(tenant_id, user_id_str, profile_id)
    if profile is None or not profile.profile.search_profiles:
        await message.answer("No profile yet. Upload a resume with /mode then send a PDF.")
        return

    sp = profile.profile.search_profiles[0]
    pos = len(sp.positive_example_texts)
    neg = len(sp.negative_example_texts)
    roles = list(sp.target_roles)[:5]
    skills = [s.canonical_name for s in sp.required_skills][:10]

    lines = [
        f"<b>Your search profile</b>",
        f"ID: <code>{profile_id}</code>",
        f"Positive examples: {pos}",
        f"Negative examples: {neg}",
    ]
    if roles:
        lines.append(f"Roles: {', '.join(roles)}")
    if skills:
        lines.append(f"Skills: {', '.join(skills)}")
    updated = profile.updated_at.strftime("%Y-%m-%d %H:%M") if profile.updated_at else "—"
    lines.append(f"Updated: {updated}")
    lines.append("\nUse /list_examples to see all examples.")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("list_examples"))
async def cmd_list_examples(message: Message, runner: TenantRunner) -> None:
    if message.text is None:
        return
    args = message.text.split()[1:]
    tenant_ids = runner.tenant_ids()
    tenant_id = tenant_ids[0] if tenant_ids else "default"
    user_id_str = str(message.from_user.id if message.from_user else 0)
    profile_id = _profile_id(user_id_str)

    profile = await runner.get_candidate_profile(tenant_id, user_id_str, profile_id)
    if profile is None or not profile.profile.search_profiles:
        await message.answer("No profile yet. Upload a resume with /mode then send a PDF.")
        return

    sp = profile.profile.search_profiles[0]
    filter_type = args[0] if args else None
    example_lines: list[str] = []

    valid_types = {"positive_resume", "negative_resume"}
    show_types: list[str] = (
        [filter_type] if filter_type in valid_types else ["positive_resume", "negative_resume"]
    )

    for ex_type in show_types:
        texts = sp.positive_example_texts if "positive" in ex_type else sp.negative_example_texts
        label = ex_type.replace("_", " ").title()
        if texts:
            example_lines.append(f"{label} ({len(texts)}):")
            for idx, t in enumerate(texts):
                preview = t[:80].replace("\n", " ")
                example_lines.append(f"  [{idx}] {preview}...")
        else:
            example_lines.append(f"{label}: none")

    await message.answer("\n".join(example_lines) if example_lines else "No examples.")


@router.message(Command("delete_example"))
async def cmd_delete_example(
    message: Message,
    runner: TenantRunner,
    embedding_provider: EmbeddingProvider | None = None,
) -> None:
    if message.text is None:
        return
    args = message.text.split()[1:]
    if len(args) < 2:
        await message.answer(
            "Usage: /delete_example <type> <index>\n"
            "Types: positive_resume, negative_resume"
        )
        return

    ex_type = args[0]
    valid_types = {"positive_resume", "negative_resume"}
    if ex_type not in valid_types:
        await message.answer(f"Invalid type. Use: {', '.join(sorted(valid_types))}")
        return

    try:
        ex_index = int(args[1])
    except ValueError:
        await message.answer("Index must be an integer.")
        return

    tenant_ids = runner.tenant_ids()
    tenant_id = tenant_ids[0] if tenant_ids else "default"
    user_id_str = str(message.from_user.id if message.from_user else 0)
    profile_id = _profile_id(user_id_str)

    profile = await runner.get_candidate_profile(tenant_id, user_id_str, profile_id)
    if not profile:
        await message.answer("No profile found.")
        return

    updated = remove_example_from_profile(profile, ex_type, ex_index)
    if embedding_provider:
        updated = await embed_profile_examples(updated, embedding_provider)

    await runner.save_candidate_profile(tenant_id, updated)
    await message.answer(f"Deleted {ex_type}[{ex_index}] from your profile.")
