"""Base handlers for the Telegram bot."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram import Router
from aiogram.filters import Command

if TYPE_CHECKING:
    from aiogram.types import Message

    from job_ftch.application.tenant_runner import TenantRunner
    from adapters.telegram_bot.config import TelegramBotConfig

router = Router(name="base")


@router.message(Command("start"))
async def cmd_start(message: Message, runner: TenantRunner) -> None:
    """Handle /start command."""
    tenant_ids = runner.tenant_ids()
    await message.answer(
        "Welcome! Available tenants: " + ", ".join(tenant_ids),
    )


@router.message(Command("tenants"))
async def cmd_tenants(message: Message, runner: TenantRunner) -> None:
    """Handle /tenants command."""
    tenants = await runner.list_tenants()
    if not tenants:
        await message.answer("No tenants found.")
        return
    text = "\n".join(f"- {item.tenant_id}: {item.display_name}" for item in tenants)
    await message.answer(text)


@router.message(Command("status"))
async def cmd_status(message: Message, runner: TenantRunner) -> None:
    """Handle /status command."""
    args = message.text.split()[1:] if message.text else []
    tenant_ids = runner.tenant_ids()
    status_tenant_id = args[0] if args else (tenant_ids[0] if tenant_ids else "default")

    summary = await runner.get_status(status_tenant_id)
    reply = (
        "No runs yet."
        if summary is None
        else (
            f"{status_tenant_id}: emitted={summary.emitted}, failed={summary.failed}, "
            f"quarantined={summary.quarantined}"
        )
    )
    await message.answer(reply)


@router.message(Command("sources"))
async def cmd_sources(
    message: Message, runner: TenantRunner, config: TelegramBotConfig
) -> None:
    """Handle /sources command."""
    args = message.text.split()[1:] if message.text else []
    tenant_ids = runner.tenant_ids()
    sources_tenant_id = args[0] if args else (tenant_ids[0] if tenant_ids else "default")

    payloads = await runner.list_sources(sources_tenant_id)
    if not payloads:
        await message.answer("No configured sources.")
        return

    is_admin = bool(message.from_user and message.from_user.id in config.admin_user_ids)
    lines = []
    for item in payloads[:10]:
        if is_admin:
            lines.append(f"{item['source_name']}: {item['status']} ({item['origin']})")
        else:
            lines.append(f"{item['source_name']}: {item['status']}")
            
    await message.answer("\n".join(lines))
