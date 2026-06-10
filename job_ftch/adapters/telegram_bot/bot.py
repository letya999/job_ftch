"""Telegram bot service, webhook parsing, and polling helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

import httpx
import structlog

from job_ftch.adapters.telegram_bot.formatter import format_job_digest, format_job_message
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.infrastructure.auth.env_auth import EnvAuthProvider

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    import asyncio


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
    token: str
    secret_token: str | None = None
    webhook_url: str | None = None
    bridge_api_key: str | None = None
    allowed_user_ids: tuple[int, ...] = ()
    allowed_chat_ids: tuple[int, ...] = ()
    admin_user_ids: tuple[int, ...] = ()
    rate_limit_seconds: float = 1.0
    digest_size: int = 5


def load_bot_config(
    auth_provider: EnvAuthProvider, source_id: str = "telegram_bot"
) -> TelegramBotConfig:
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
    )


class TelegramSender(Protocol):
    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> None: ...


class HttpTelegramBotClient:
    def __init__(self, token: str, *, timeout: float = 10.0) -> None:
        self._base_url = f"https://api.telegram.org/bot{token}"
        self._client = httpx.AsyncClient(timeout=timeout)

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        response = await self._client.post(f"{self._base_url}/sendMessage", json=payload)
        response.raise_for_status()

    async def set_webhook(self, url: str, *, secret_token: str | None = None) -> None:
        payload: dict[str, Any] = {"url": url}
        if secret_token is not None:
            payload["secret_token"] = secret_token
        response = await self._client.post(f"{self._base_url}/setWebhook", json=payload)
        response.raise_for_status()

    async def get_updates(
        self, *, offset: int | None = None, timeout: int = 30
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        response = await self._client.post(f"{self._base_url}/getUpdates", json=payload)
        response.raise_for_status()
        payload = response.json()
        result = payload.get("result", [])
        return cast("list[dict[str, Any]]", result)

    async def close(self) -> None:
        await self._client.aclose()


class TelegramBotService:
    def __init__(
        self,
        *,
        runner: TenantRunner,
        sender: TelegramSender,
        config: TelegramBotConfig,
    ) -> None:
        self._runner = runner
        self._sender = sender
        self._config = config
        self._last_seen_at: dict[int, float] = {}

    @property
    def config(self) -> TelegramBotConfig:
        return self._config

    async def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or update.get("callback_query", {}).get("message")
        if not isinstance(message, dict):
            return
        chat_id = int(message["chat"]["id"])
        user = message.get("from") or update.get("callback_query", {}).get("from") or {}
        user_id = int(user.get("id", 0))

        callback = update.get("callback_query")
        if isinstance(callback, dict) and isinstance(callback.get("data"), str):
            await self._handle_callback(callback["data"], chat_id=chat_id, user_id=user_id)
            return

        text = message.get("text")
        if not isinstance(text, str):
            return
        await self.handle_command(text, chat_id=chat_id, user_id=user_id)

    async def handle_command(self, text: str, *, chat_id: int, user_id: int) -> None:
        if not self._is_allowed(user_id=user_id, chat_id=chat_id):
            logger.warning("telegram_bot_access_denied", chat_id=chat_id, user_id=user_id)
            await self._sender.send_message(chat_id, "Access denied.")
            return
        if self._is_throttled(user_id):
            await self._sender.send_message(chat_id, "Too many requests. Try again shortly.")
            return

        command, _, arg_text = text.partition(" ")
        args = [part for part in arg_text.split() if part]
        tenant_ids = self._runner.tenant_ids()

        if command == "/start":
            await self._sender.send_message(
                chat_id,
                "Available tenants: " + ", ".join(tenant_ids),
            )
            return
        if command == "/tenants":
            tenants = await self._runner.list_tenants()
            text = "\n".join(f"- {item.tenant_id}: {item.display_name}" for item in tenants)
            await self._sender.send_message(chat_id, text)
            return
        if command == "/status":
            status_tenant_id = args[0] if args else tenant_ids[0]
            summary = await self._runner.get_status(status_tenant_id)
            reply = (
                "No runs yet."
                if summary is None
                else (
                    f"{status_tenant_id}: emitted={summary.emitted}, failed={summary.failed}, "
                    f"quarantined={summary.quarantined}"
                )
            )
            await self._sender.send_message(chat_id, reply)
            return
        if command == "/run":
            self._require_admin(user_id)
            tenant_id: str | None = args[0] if args else None
            if tenant_id is None:
                summaries = await self._runner.run_all()
                await self._sender.send_message(chat_id, f"Ran {len(summaries)} tenant(s).")
                return
            summary = await self._runner.run_tenant(tenant_id)
            await self._sender.send_message(chat_id, f"{tenant_id}: emitted={summary.emitted}")
            return
        if command == "/reset":
            self._require_admin(user_id)
            tenant_id = args[0]
            await self._runner.reset_tenant(tenant_id)
            await self._sender.send_message(chat_id, f"Reset {tenant_id}")
            return
        if command == "/digest":
            digest_tenant_id = args[0] if args else tenant_ids[0]
            jobs = await self._runner.latest_jobs(
                digest_tenant_id, limit=self._config.digest_size * 3
            )
            await self._sender.send_message(
                chat_id,
                format_job_digest(jobs, page=0, page_size=self._config.digest_size),
                reply_markup=_build_pagination_markup(
                    prefix="digest",
                    query=digest_tenant_id,
                    page=1,
                    tenant_id=digest_tenant_id,
                ),
            )
            return
        if command == "/search":
            search_tenant_id: str | None = None
            if args and args[-1] in tenant_ids:
                search_tenant_id = args[-1]
                args = args[:-1]
            query = " ".join(args)
            results = await self._runner.search_jobs(query, tenant_id=search_tenant_id, limit=10)
            if not results:
                await self._sender.send_message(chat_id, "No matches.")
                return
            group = results[0]
            await self._sender.send_message(
                chat_id,
                format_job_message(group.canonical_job),
                reply_markup=_build_pagination_markup(
                    prefix="search",
                    query=query,
                    page=1,
                    tenant_id=search_tenant_id,
                    url=str(group.canonical_job.canonical_url or ""),
                ),
            )
            return
        await self._sender.send_message(chat_id, "Unknown command.")

    async def _handle_callback(self, data: str, *, chat_id: int, user_id: int) -> None:
        if not self._is_allowed(user_id=user_id, chat_id=chat_id):
            await self._sender.send_message(chat_id, "Access denied.")
            return
        parts = data.split("|")
        if len(parts) < 4:
            return
        prefix, tenant_id, page_text, query = parts[:4]
        page = int(page_text)
        if prefix == "digest":
            jobs = await self._runner.latest_jobs(
                tenant_id, limit=(page + 1) * self._config.digest_size
            )
            await self._sender.send_message(
                chat_id,
                format_job_digest(jobs, page=page, page_size=self._config.digest_size),
            )
            return
        if prefix == "search":
            tenant_value = None if tenant_id == "-" else tenant_id
            groups = await self._runner.search_jobs(
                query, tenant_id=tenant_value, limit=(page + 1) * 5
            )
            if page < len(groups):
                await self._sender.send_message(
                    chat_id, format_job_message(groups[page].canonical_job)
                )

    def _is_allowed(self, *, user_id: int, chat_id: int) -> bool:
        allowed_users = self._config.allowed_user_ids
        allowed_chats = self._config.allowed_chat_ids
        user_ok = not allowed_users or user_id in allowed_users
        chat_ok = not allowed_chats or chat_id in allowed_chats
        return user_ok and chat_ok

    def _is_throttled(self, user_id: int) -> bool:
        now = time.monotonic()
        last_seen = self._last_seen_at.get(user_id)
        if last_seen is not None and now - last_seen < self._config.rate_limit_seconds:
            return True
        self._last_seen_at[user_id] = now
        return False

    def _require_admin(self, user_id: int) -> None:
        if self._config.admin_user_ids and user_id not in self._config.admin_user_ids:
            msg = "Admin privileges required."
            raise PermissionError(msg)


def _build_pagination_markup(
    *,
    prefix: str,
    query: str,
    page: int,
    tenant_id: str | None,
    url: str | None = None,
) -> dict[str, Any]:
    tenant_part = tenant_id or "-"
    buttons = []
    if url:
        buttons.append([{"text": "Open URL", "url": url}])
    buttons.append(
        [{"text": "Next page", "callback_data": f"{prefix}|{tenant_part}|{page}|{query}"}]
    )
    return {"inline_keyboard": buttons}


async def run_polling_loop(
    *,
    service: TelegramBotService,
    client: HttpTelegramBotClient,
    stop_event: asyncio.Event | None = None,
) -> None:
    next_offset: int | None = None
    while stop_event is None or not stop_event.is_set():
        updates = await client.get_updates(offset=next_offset)
        for update in updates:
            next_offset = int(update["update_id"]) + 1
            await service.handle_update(update)
