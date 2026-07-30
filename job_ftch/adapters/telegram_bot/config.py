"""Configuration for the Telegram bot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_ftch.infrastructure.auth.env_auth import EnvAuthProvider


def _parse_csv_ints(raw: str | None) -> tuple[int, ...]:
    if raw is None:
        return ()
    values = []
    for token in raw.split(","):
        stripped = token.strip()
        if stripped:
            values.append(int(stripped))
    return tuple(values)


@dataclass(frozen=True)
class TelegramBotConfig:
    """Configuration for the Telegram bot."""

    token: str
    secret_token: str | None = None
    webhook_url: str | None = None
    bridge_api_key: str | None = None
    allowed_user_ids: tuple[int, ...] = ()
    allowed_chat_ids: tuple[int, ...] = ()
    admin_user_ids: tuple[int, ...] = ()
    rate_limit_seconds: float = 1.0
    digest_size: int = 5
    open_access: bool = False


def load_bot_config(
    auth_provider: EnvAuthProvider, source_id: str = "telegram_bot"
) -> TelegramBotConfig:
    """Load bot configuration from auth provider."""
    payload = auth_provider.resolve(source_id)
    token = payload.get("token")
    if not token:
        msg = "Telegram bot token is required under JOB_FTCH_AUTH_TELEGRAM_BOT_TOKEN."
        raise ValueError(msg)
    return TelegramBotConfig(
        token=token,
        secret_token=payload.get("secret_token"),
        webhook_url=payload.get("webhook_url"),
        bridge_api_key=payload.get("bridge_api_key"),
        allowed_user_ids=_parse_csv_ints(payload.get("allowed_user_ids")),
        allowed_chat_ids=_parse_csv_ints(payload.get("allowed_chat_ids")),
        admin_user_ids=_parse_csv_ints(payload.get("admin_user_ids")),
        rate_limit_seconds=float(payload.get("rate_limit_seconds", "1.0")),
        digest_size=int(payload.get("digest_size", "5")),
        open_access=payload.get("open_access", "false").lower() == "true",
    )
