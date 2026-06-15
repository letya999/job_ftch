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

_COMMON_COMMANDS: tuple[tuple[str, str], ...] = (
    ("start", "Show the welcome message"),
    ("help", "Show available commands"),
    ("status", "Show latest pipeline status"),
    ("sources", "List configured sources"),
    ("search", "Search jobs in the catalog"),
    ("digest", "Browse jobs one by one"),
    ("profiles", "Your search profile and example counts"),
    ("list_examples", "List your positive/negative resume examples"),
    ("delete_example", "Delete one stored example by type and index"),
    ("mode", "Set upload mode (positive/negative resume)"),
)

_ADMIN_COMMANDS: tuple[tuple[str, str], ...] = (
    ("run", "Run the pipeline now"),
    ("reset", "Reset runtime state"),
    ("reset_dedup", "Clear dedup records (dev)"),
    ("addsource", "Add one source"),
    ("addsources", "Bulk add sources"),
    ("disablesource", "Disable a source"),
    ("setposting", "Configure posting backend"),
    ("setnotify", "Configure notification mode"),
)


def render_help_text(config: TelegramBotConfig) -> str:
    """Render a compact help message for the current user role."""
    lines = ["Available commands:", ""]
    lines.extend(f"/{name} - {description}" for name, description in _COMMON_COMMANDS)
    if config.admin_user_ids:
        lines.append("")
        lines.append("Admin commands:")
        lines.extend(f"/{name} - {description}" for name, description in _ADMIN_COMMANDS)
    return "\n".join(lines)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Handle /start command."""
    await message.answer(
        "Welcome! Upload example resumes with /mode, then use /run and /digest "
        "to find matching jobs.\n\nSee /help for all commands.",
    )


@router.message(Command("help"))
async def cmd_help(message: Message, config: TelegramBotConfig) -> None:
    """Handle /help command."""
    await message.answer(render_help_text(config))


@router.message(Command("status"))
async def cmd_status(message: Message, runner: TenantRunner) -> None:
    """Handle /status command."""
    status_tenant_id = runner.default_tenant_id()

    summary = await runner.get_status(status_tenant_id)
    reply = (
        "No runs yet."
        if summary is None
        else (
            f"emitted={summary.emitted}, failed={summary.failed}, "
            f"quarantined={summary.quarantined}"
        )
    )
    await message.answer(reply)


@router.message(Command("sources"))
async def cmd_sources(
    message: Message, runner: TenantRunner, config: TelegramBotConfig
) -> None:
    """Handle /sources command."""
    sources_tenant_id = runner.default_tenant_id()

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
