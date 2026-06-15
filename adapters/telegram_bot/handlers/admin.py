"""Admin handlers for the Telegram bot."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from aiogram import Router
from aiogram.filters import Command

from adapters.telegram_bot.filters.role import IsAdminFilter
from job_ftch.application.source_inputs import build_source_spec_from_input
from job_ftch.application.source_validator import validate_sources

if TYPE_CHECKING:
    from aiogram.types import Message

    from job_ftch.application.tenant_runner import TenantRunner

router = Router(name="admin")
# Apply IsAdminFilter to all handlers in this router
router.message.filter(IsAdminFilter())


@router.message(Command("run"))
async def cmd_run(message: Message, runner: TenantRunner) -> None:
    """Handle /run command."""
    args = message.text.split()[1:] if message.text else []
    run_tenant_id = args[0] if args else None

    if run_tenant_id is None:
        summaries = await runner.run_all()
        await message.answer(f"Ran {len(summaries)} tenant(s).")
        return

    summary = await runner.run_tenant(run_tenant_id)
    await message.answer(f"{run_tenant_id}: emitted={summary.emitted}")


@router.message(Command("reset"))
async def cmd_reset(message: Message, runner: TenantRunner) -> None:
    """Handle /reset command."""
    args = message.text.split()[1:] if message.text else []
    if not args:
        await message.answer("Usage: /reset <tenant_id>")
        return

    tenant_id = args[0]
    await runner.reset_tenant(tenant_id)
    await message.answer(f"Reset {tenant_id}")


@router.message(Command("addsource"))
async def cmd_addsource(message: Message, runner: TenantRunner) -> None:
    """Handle /addsource command."""
    args = message.text.split()[1:] if message.text else []
    if len(args) < 2:
        await message.answer("Usage: /addsource <tenant_id> <link>")
        return

    add_tenant_id, link = args[0], args[1]
    spec = await build_source_spec_from_input(
        link,
        auth_provider=runner.get_runtime(add_tenant_id).auth_provider,
    )
    payload = await runner.add_source_spec(
        add_tenant_id,
        spec,
        added_via="telegram_bot",
        added_by=str(message.from_user.id if message.from_user else 0),
        input_value=link,
    )
    await message.answer(f"Added {payload['source_id']} to {add_tenant_id}.")


@router.message(Command("disablesource"))
async def cmd_disablesource(message: Message, runner: TenantRunner) -> None:
    """Handle /disablesource command."""
    args = message.text.split()[1:] if message.text else []
    if len(args) < 2:
        await message.answer("Usage: /disablesource <tenant_id> <source_id>")
        return

    disabled = await runner.disable_source(args[0], args[1])
    await message.answer(f"Disabled {disabled['source_id']} in {args[0]}.")


@router.message(Command("setposting"))
async def cmd_setposting(message: Message, runner: TenantRunner) -> None:
    """Handle /setposting command."""
    args = message.text.split()[1:] if message.text else []
    if len(args) < 2:
        await message.answer("Usage: /setposting <tenant_id> <channel_id_or_username>")
        return

    post_tenant_id, channel = args[0], args[1]
    await runner.update_posting_config(post_tenant_id, channel)
    await message.answer(
        f"Posting enabled for {post_tenant_id} to {channel}. Backend: telegram_posting."
    )


@router.message(Command("setnotify"))
async def cmd_setnotify(message: Message, runner: TenantRunner) -> None:
    """Handle /setnotify command."""
    args = message.text.split()[1:] if message.text else []
    if len(args) < 2:
        await message.answer("Usage: /setnotify <tenant_id> <instant|digest> [batch_size]")
        return

    notify_tenant_id, mode = args[0], args[1].lower()
    batch_size = None
    if len(args) >= 3:
        with contextlib.suppress(ValueError):
            batch_size = int(args[2])

    try:
        await runner.update_notify_config(notify_tenant_id, mode, batch_size)
        msg = f"Notification mode for {notify_tenant_id} set to {mode}."
        if batch_size:
            msg += f" Batch size: {batch_size}."
        await message.answer(msg)
    except ValueError as exc:
        await message.answer(str(exc))


@router.message(Command("reset_dedup"))
async def cmd_reset_dedup(message: Message, runner: TenantRunner) -> None:
    """Handle /reset_dedup command."""
    args = message.text.split()[1:] if message.text else []
    tenant_ids = args if args else runner.tenant_ids()

    total = 0
    for tid in tenant_ids:
        count = await runner.clear_dedup(tid)
        total += count

    await message.answer(
        f"Dedup cleared for {len(tenant_ids)} tenant(s). {total} records removed."
    )


@router.message(Command("addsources"))
async def cmd_addsources(message: Message, runner: TenantRunner) -> None:
    """Handle /addsources command."""
    args = message.text.split()[1:] if message.text else []
    if len(args) < 2:
        await message.answer("Usage: /addsources <tenant_id> <link1> <link2>...")
        return

    tenant_id = args[0]
    links = args[1:]

    validation = await validate_sources(links)
    valid_links = [link for link in links if validation[link][0]]
    failed_validation = [
        (link, validation[link][1]) for link in links if not validation[link][0]
    ]

    if failed_validation:
        fail_msg = "The following sources are unreachable:\n" + "\n".join(
            f"  {link}: {reason}" for link, reason in failed_validation
        )
        fail_msg += "\nPlease fix them and resend."
        await message.answer(fail_msg)
        return

    added_count = 0
    errors = []
    for link in valid_links:
        try:
            spec = await build_source_spec_from_input(
                link,
                auth_provider=runner.get_runtime(tenant_id).auth_provider,
            )
            await runner.add_source_spec(tenant_id, spec, input_value=link)
            added_count += 1
        except Exception as exc:
            errors.append(f"{link}: {exc}")

    total = len(links)
    if not errors:
        await message.answer(f"All {added_count} sources added.")
    else:
        msg = f"Added {added_count}/{total} sources.\nFailed:\n" + "\n".join(errors)
        await message.answer(msg)
