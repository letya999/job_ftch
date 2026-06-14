"""Profile handlers for the Telegram bot."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from aiogram import Router
from aiogram.filters import Command

from job_ftch.application.profile_inputs import (
    build_candidate_profile_from_payload,
    embed_profile_examples,
    remove_example_from_profile,
)
from job_ftch.domain import ManagedCandidateProfile

if TYPE_CHECKING:
    from aiogram.types import Message

    from job_ftch.application.contracts import EmbeddingProvider
    from job_ftch.application.tenant_runner import TenantRunner

router = Router(name="profiles")


@router.message(Command("profiles"))
async def cmd_profiles(message: Message, runner: TenantRunner) -> None:
    """Handle /profiles command."""
    if message.text is None:
        return
    args = message.text.split()[1:]
    tenant_ids = runner.tenant_ids()
    profiles_tenant_id = args[0] if args else (tenant_ids[0] if tenant_ids else "default")

    payloads = await runner.list_candidate_profiles(
        profiles_tenant_id,
        str(message.from_user.id if message.from_user else 0),
    )
    if not payloads:
        await message.answer("No profiles yet.")
        return
    lines = [
        f"{item['profile_id']}: {'active' if item['active'] else 'inactive'}"
        for item in payloads
    ]
    await message.answer("\n".join(lines))


@router.message(Command("saveprofile"))
async def cmd_saveprofile(message: Message, runner: TenantRunner) -> None:
    """Handle /saveprofile command."""
    if message.text is None:
        return
    args = message.text.split()[1:]
    if len(args) < 3:
        await message.answer("Usage: /saveprofile <tenant_id> <profile_id> <summary>")
        return

    profile_tenant_id = args[0]
    profile_id = args[1]
    # Re-extract summary to keep spaces
    parts = message.text.split(" ", maxsplit=3)
    profile_summary = parts[3].strip() if len(parts) > 3 else ""

    user_id_str = str(message.from_user.id if message.from_user else 0)

    candidate_profile = build_candidate_profile_from_payload(
        user_id=user_id_str,
        profile_id=profile_id,
        payload={"summary": profile_summary, "name": profile_id},
    )
    profile_payload = await runner.save_candidate_profile(
        profile_tenant_id,
        ManagedCandidateProfile(
            user_id=user_id_str,
            profile_id=profile_id,
            profile=candidate_profile,
            updated_at=datetime.now(UTC),
        ),
    )
    await runner.set_active_candidate_profile(
        profile_tenant_id, user_id_str, profile_id
    )
    await message.answer(
        f"Saved profile {profile_payload['profile_id']} for {profile_tenant_id}."
    )


@router.message(Command("activateprofile"))
async def cmd_activateprofile(message: Message, runner: TenantRunner) -> None:
    """Handle /activateprofile command."""
    if message.text is None:
        return
    args = message.text.split()[1:]
    if len(args) < 2:
        await message.answer("Usage: /activateprofile <tenant_id> <profile_id>")
        return

    user_id_str = str(message.from_user.id if message.from_user else 0)
    payload = await runner.set_active_candidate_profile(
        args[0],
        user_id_str,
        args[1],
    )
    await message.answer(f"Activated profile {payload['profile_id']} in {args[0]}.")


@router.message(Command("list_examples"))
async def cmd_list_examples(message: Message, runner: TenantRunner) -> None:
    """Handle /list_examples command."""
    if message.text is None:
        return
    args = message.text.split()[1:]
    tenant_ids = runner.tenant_ids()
    tenant_id = tenant_ids[0] if tenant_ids else "default"
    user_id_str = str(message.from_user.id if message.from_user else 0)

    profiles = await runner.list_candidate_profiles(tenant_id, user_id_str)
    active_profile_payload = next((p for p in profiles if p["active"]), None)
    if not active_profile_payload:
        await message.answer("No active profile. Upload a resume first.")
        return

    active_profile = await runner.get_candidate_profile(
        tenant_id, user_id_str, active_profile_payload["profile_id"]
    )
    if not active_profile or not active_profile.profile.search_profiles:
        await message.answer("No examples found in active profile.")
        return

    sp = active_profile.profile.search_profiles[0]
    filter_type = args[0] if args else None
    example_lines: list[str] = []

    valid_types = {"positive_resume", "negative_resume", "positive_job", "negative_job"}
    show_types: list[str] = (
        [filter_type]
        if filter_type in valid_types
        else ["positive_resume", "negative_resume"]
    )

    for ex_type in show_types:
        texts = (
            sp.positive_example_texts
            if "positive" in ex_type
            else sp.negative_example_texts
        )
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
    """Handle /delete_example command."""
    if message.text is None:
        return
    args = message.text.split()[1:]
    if len(args) < 2:
        await message.answer(
            "Usage: /delete_example <type> <index>\n"
            "Types: positive_resume, negative_resume, positive_job, negative_job"
        )
        return

    ex_type = args[0]
    valid_types = {"positive_resume", "negative_resume", "positive_job", "negative_job"}
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

    profiles = await runner.list_candidate_profiles(tenant_id, user_id_str)
    active_profile_payload = next((p for p in profiles if p["active"]), None)
    if not active_profile_payload:
        await message.answer("No active profile found.")
        return

    active_profile = await runner.get_candidate_profile(
        tenant_id, user_id_str, active_profile_payload["profile_id"]
    )
    if not active_profile:
        await message.answer("Could not load active profile.")
        return

    updated_profile = remove_example_from_profile(active_profile, ex_type, ex_index)
    if embedding_provider:
        updated_profile = await embed_profile_examples(
            updated_profile, embedding_provider
        )

    await runner.save_candidate_profile(tenant_id, updated_profile)
    await message.answer(f"Deleted {ex_type}[{ex_index}] from your profile.")
