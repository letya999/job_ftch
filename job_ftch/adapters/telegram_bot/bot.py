"""Telegram bot service, webhook parsing, and polling helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, cast

import httpx
import structlog

from job_ftch.adapters.profile_inputs import (
    build_candidate_profile_from_payload,
    build_profile_from_resume_text,
)
from job_ftch.adapters.source_inputs import build_source_spec_from_input
from job_ftch.adapters.telegram_bot.formatter import format_job_digest, format_job_message
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.domain import ManagedCandidateProfile
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
        self._token = token
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

    async def download_file(self, file_id: str) -> bytes:
        response = await self._client.get(f"{self._base_url}/getFile", params={"file_id": file_id})
        response.raise_for_status()
        file_path = response.json()["result"]["file_path"]

        download_url = f"https://api.telegram.org/file/bot{self._token}/{file_path}"
        response = await self._client.get(download_url)
        response.raise_for_status()
        return response.content

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
        document = message.get("document")

        if document and not isinstance(text, str):
            await self.handle_document(document, chat_id=chat_id, user_id=user_id)
            return

        if not isinstance(text, str):
            return
        await self.handle_command(text, chat_id=chat_id, user_id=user_id)

    async def handle_document(self, document: dict[str, Any], *, chat_id: int, user_id: int) -> None:
        if not self._is_allowed(user_id=user_id, chat_id=chat_id):
            return

        file_id = document.get("file_id")
        file_name = document.get("file_name", "resume.txt")
        if not file_id:
            return

        await self._sender.send_message(chat_id, "Processing your resume...")

        try:
            content = await self._sender.download_file(file_id)
            text = self._extract_text(content, file_name)
            if not text.strip():
                await self._sender.send_message(chat_id, "Could not extract text from the file.")
                return

            # Default to first tenant if multiple
            tenant_ids = self._runner.tenant_ids()
            tenant_id = tenant_ids[0] if tenant_ids else "default"

            managed_profile = build_profile_from_resume_text(text, user_id=str(user_id))
            await self._runner.save_candidate_profile(tenant_id, managed_profile)
            await self._runner.set_active_candidate_profile(
                tenant_id, str(user_id), managed_profile.profile_id
            )

            await self._sender.send_message(
                chat_id,
                f"Profile created from your resume. Active: {managed_profile.profile_id}",
            )
        except Exception as exc:
            logger.error("resume_upload_failed", error=str(exc), exc_info=True)
            await self._sender.send_message(chat_id, f"Error processing resume: {exc}")

    def _extract_text(self, content: bytes, filename: str) -> str:
        import io

        if filename.lower().endswith(".pdf"):
            try:
                from pypdf import PdfReader

                reader = PdfReader(io.BytesIO(content))
                return "\n".join(page.extract_text() or "" for page in reader.pages)
            except ImportError:
                return "PDF extraction requires 'pypdf' library."

        if filename.lower().endswith(".docx"):
            try:
                from docx import Document

                doc = Document(io.BytesIO(content))
                return "\n".join(p.text for p in doc.paragraphs)
            except ImportError:
                return "DOCX extraction requires 'python-docx' library."

        if filename.lower().endswith(".txt"):
            return content.decode("utf-8", errors="replace")

        return ""

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
        if command == "/sources":
            sources_tenant_id = args[0] if args else tenant_ids[0]
            payloads = await self._runner.list_sources(sources_tenant_id)
            if not payloads:
                await self._sender.send_message(chat_id, "No configured sources.")
                return
            lines = [
                f"{item['source_name']}: {item['status']} ({item['origin']})"
                for item in payloads[:10]
            ]
            await self._sender.send_message(chat_id, "\n".join(lines))
            return
        if command == "/profiles":
            profiles_tenant_id = args[0] if args else tenant_ids[0]
            payloads = await self._runner.list_candidate_profiles(
                profiles_tenant_id,
                str(user_id),
            )
            if not payloads:
                await self._sender.send_message(chat_id, "No profiles yet.")
                return
            lines = [
                f"{item['profile_id']}: {'active' if item['active'] else 'inactive'}"
                for item in payloads
            ]
            await self._sender.send_message(chat_id, "\n".join(lines))
            return
        if command == "/saveprofile":
            if len(args) < 3:
                await self._sender.send_message(
                    chat_id,
                    "Usage: /saveprofile <tenant_id> <profile_id> <summary>",
                )
                return
            profile_tenant_id = args[0]
            profile_id = args[1]
            profile_summary = arg_text.split(" ", maxsplit=2)[2].strip()
            candidate_profile = build_candidate_profile_from_payload(
                user_id=str(user_id),
                profile_id=profile_id,
                payload={"summary": profile_summary, "name": profile_id},
            )
            profile_payload = await self._runner.save_candidate_profile(
                profile_tenant_id,
                ManagedCandidateProfile(
                    user_id=str(user_id),
                    profile_id=profile_id,
                    profile=candidate_profile,
                    updated_at=datetime.now(UTC),
                ),
            )
            await self._runner.set_active_candidate_profile(
                profile_tenant_id, str(user_id), profile_id
            )
            await self._sender.send_message(
                chat_id,
                f"Saved profile {profile_payload['profile_id']} for {profile_tenant_id}.",
            )
            return
        if command == "/activateprofile":
            if len(args) < 2:
                await self._sender.send_message(
                    chat_id,
                    "Usage: /activateprofile <tenant_id> <profile_id>",
                )
                return
            payload = await self._runner.set_active_candidate_profile(
                args[0],
                str(user_id),
                args[1],
            )
            await self._sender.send_message(
                chat_id,
                f"Activated profile {payload['profile_id']} in {args[0]}.",
            )
            return
        if command == "/addsource":
            try:
                self._require_admin(user_id)
            except PermissionError as exc:
                await self._sender.send_message(chat_id, f"Access denied: {exc}")
                return
            if len(args) < 2:
                await self._sender.send_message(chat_id, "Usage: /addsource <tenant_id> <link>")
                return
            add_tenant_id, link = args[0], args[1]
            spec = await build_source_spec_from_input(
                link,
                auth_provider=self._runner.get_runtime(add_tenant_id).auth_provider,
            )
            payload = await self._runner.add_source_spec(
                add_tenant_id,
                spec,
                added_via="telegram_bot",
                added_by=str(user_id),
                input_value=link,
            )
            await self._sender.send_message(
                chat_id,
                f"Added {payload['source_id']} to {add_tenant_id}.",
            )
            return
        if command == "/disablesource":
            try:
                self._require_admin(user_id)
            except PermissionError as exc:
                await self._sender.send_message(chat_id, f"Access denied: {exc}")
                return
            if len(args) < 2:
                await self._sender.send_message(
                    chat_id,
                    "Usage: /disablesource <tenant_id> <source_id>",
                )
                return
            disabled = await self._runner.disable_source(args[0], args[1])
            await self._sender.send_message(
                chat_id,
                f"Disabled {disabled['source_id']} in {args[0]}.",
            )
            return
        if command == "/setposting":
            try:
                self._require_admin(user_id)
            except PermissionError as exc:
                await self._sender.send_message(chat_id, f"Access denied: {exc}")
                return
            if len(args) < 2:
                await self._sender.send_message(
                    chat_id, "Usage: /setposting <tenant_id> <channel_id_or_username>"
                )
                return
            post_tenant_id, channel = args[0], args[1]
            await self._runner.update_posting_config(post_tenant_id, channel)
            await self._sender.send_message(
                chat_id,
                f"Posting enabled for {post_tenant_id} to {channel}. "
                "Backend: telegram_posting.",
            )
            return
        if command == "/setnotify":
            try:
                self._require_admin(user_id)
            except PermissionError as exc:
                await self._sender.send_message(chat_id, f"Access denied: {exc}")
                return
            if len(args) < 2:
                await self._sender.send_message(
                    chat_id, "Usage: /setnotify <tenant_id> <instant|digest>"
                )
                return
            notify_tenant_id, mode = args[0], args[1].lower()
            try:
                await self._runner.update_notify_config(notify_tenant_id, mode)
                await self._sender.send_message(
                    chat_id,
                    f"Notification mode for {notify_tenant_id} set to {mode}.",
                )
            except ValueError as exc:
                await self._sender.send_message(chat_id, str(exc))
            return
        if command == "/addsources":
            try:
                self._require_admin(user_id)
            except PermissionError as exc:
                await self._sender.send_message(chat_id, f"Access denied: {exc}")
                return
            if len(args) < 2:
                await self._sender.send_message(
                    chat_id, "Usage: /addsources <tenant_id> <link1> <link2>..."
                )
                return
            tenant_id = args[0]
            links = args[1:]

            added_count = 0
            errors = []
            for link in links:
                try:
                    spec = await build_source_spec_from_input(
                        link,
                        auth_provider=self._runner.get_runtime(tenant_id).auth_provider,
                    )
                    await self._runner.add_source_spec(tenant_id, spec, input_value=link)
                    added_count += 1
                except Exception as exc:
                    errors.append(f"{link}: {exc}")

            msg = f"Added {added_count} sources to {tenant_id}."
            if errors:
                msg += "\n\nErrors:\n" + "\n".join(errors)
            await self._sender.send_message(chat_id, msg)
            return
        if command == "/run":
            try:
                self._require_admin(user_id)
            except PermissionError as exc:
                await self._sender.send_message(chat_id, f"Access denied: {exc}")
                return
            tenant_id: str | None = args[0] if args else None
            if tenant_id is None:
                summaries = await self._runner.run_all()
                await self._sender.send_message(chat_id, f"Ran {len(summaries)} tenant(s).")
                return
            summary = await self._runner.run_tenant(tenant_id)
            await self._sender.send_message(chat_id, f"{tenant_id}: emitted={summary.emitted}")
            return
        if command == "/reset":
            try:
                self._require_admin(user_id)
            except PermissionError as exc:
                await self._sender.send_message(chat_id, f"Access denied: {exc}")
                return
            tenant_id = args[0]
            await self._runner.reset_tenant(tenant_id)
            await self._sender.send_message(chat_id, f"Reset {tenant_id}")
            return
        if command == "/digest":
            digest_tenant_id = args[0] if args else tenant_ids[0]
            jobs = await self._runner.latest_jobs(
                digest_tenant_id,
                limit=self._config.digest_size * 3,
                user_id=str(user_id),
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
            results = await self._runner.search_jobs(
                query,
                tenant_id=search_tenant_id,
                user_id=str(user_id),
                limit=10,
            )
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
