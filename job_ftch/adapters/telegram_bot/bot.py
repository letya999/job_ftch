"""Telegram bot service, webhook parsing, and polling helpers."""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, cast

import httpx
import structlog

from job_ftch.adapters.document_parser import parse_document
from job_ftch.adapters.profile_inputs import (
    add_example_to_profile,
    build_candidate_profile_from_payload,
    build_profile_from_resume_text,
    embed_profile_examples,
)
from job_ftch.adapters.source_inputs import build_source_spec_from_input
from job_ftch.adapters.source_validator import validate_sources
from job_ftch.adapters.telegram_bot.formatter import format_job_digest, format_job_message
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.domain import ManagedCandidateProfile
from job_ftch.infrastructure.auth.env_auth import EnvAuthProvider

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    import asyncio

    from job_ftch.application.contracts import CrossEncoderPort


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
    open_access: bool = False


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
        open_access=payload.get("open_access", "false").lower() == "true",
    )


class TelegramSender(Protocol):
    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> None: ...

    async def download_file(self, file_id: str) -> bytes: ...


class HttpTelegramBotClient:
    def __init__(self, token: str, *, timeout: float = 10.0) -> None:
        self._token = token
        self._base_url = f"https://api.telegram.org/bot{token}"
        self._file_base_url = f"https://api.telegram.org/file/bot{token}"
        self._client = httpx.AsyncClient(timeout=timeout)

    async def download_file(self, file_id: str) -> bytes:
        response = await self._client.get(f"{self._base_url}/getFile", params={"file_id": file_id})
        response.raise_for_status()
        file_path = response.json()["result"]["file_path"]
        file_response = await self._client.get(f"{self._file_base_url}/{file_path}")
        file_response.raise_for_status()
        return file_response.content

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
        embedding_provider: object | None = None,
        reranker: CrossEncoderPort | None = None,
    ) -> None:
        self._runner = runner
        self._sender = sender
        self._config = config
        self._embedding_provider = embedding_provider
        self._reranker = reranker
        self._last_seen_at: dict[int, float] = {}
        self._upload_mode: dict[int, str] = {}  # user_id -> mode

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

        if not self._is_allowed(user_id=user_id, chat_id=chat_id):
            logger.warning("telegram_bot_access_denied", chat_id=chat_id, user_id=user_id)
            return  # silently deny at update level, no reply to prevent enumeration

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

    async def handle_document(
        self, document: dict[str, Any], *, chat_id: int, user_id: int
    ) -> None:
        if not self._is_allowed(user_id=user_id, chat_id=chat_id):
            return

        file_id = document.get("file_id")
        file_name = document.get("file_name", "resume.txt")
        if not file_id:
            return

        mode = self._upload_mode.get(user_id, "positive_resume")
        await self._sender.send_message(chat_id, f"Processing your {mode.replace('_', ' ')}...")

        try:
            content = await self._sender.download_file(file_id)
            text = self._extract_text(content, file_name)
            if not text.strip():
                await self._sender.send_message(chat_id, "Could not extract text from the file.")
                return

            # Default to first tenant if multiple
            tenant_ids = self._runner.tenant_ids()
            tenant_id = tenant_ids[0] if tenant_ids else "default"

            if mode == "positive_resume":
                managed_profile = build_profile_from_resume_text(text, user_id=str(user_id))
                if self._embedding_provider:
                    managed_profile = await embed_profile_examples(
                        managed_profile, self._embedding_provider
                    )
                await self._runner.save_candidate_profile(tenant_id, managed_profile)
                await self._runner.set_active_candidate_profile(
                    tenant_id, str(user_id), managed_profile.profile_id
                )
                await self._sender.send_message(
                    chat_id,
                    f"Profile created from your resume. Active: {managed_profile.profile_id}",
                )
            elif mode == "negative_resume":
                # Load existing active profile if any
                profiles = await self._runner.list_candidate_profiles(tenant_id, str(user_id))
                active_profile_payload = next((p for p in profiles if p["active"]), None)

                if active_profile_payload:
                    existing_profile = await self._runner.get_candidate_profile(
                        tenant_id, str(user_id), active_profile_payload["profile_id"]
                    )
                    if existing_profile:
                        updated_profile = add_example_to_profile(
                            existing_profile, text, kind="negative_resume"
                        )
                        if self._embedding_provider:
                            updated_profile = await embed_profile_examples(
                                updated_profile, self._embedding_provider
                            )
                        await self._runner.save_candidate_profile(tenant_id, updated_profile)
                        await self._sender.send_message(
                            chat_id, "Negative resume example added to your active profile."
                        )
                    else:
                        await self._sender.send_message(
                            chat_id, "Error: Could not load your active profile."
                        )
                else:
                    # Create new one but mark example as negative
                    managed_profile = build_profile_from_resume_text(text, user_id=str(user_id))
                    updated_profile = add_example_to_profile(
                        managed_profile, text, kind="negative_resume"
                    )
                    await self._runner.save_candidate_profile(tenant_id, updated_profile)
                    await self._runner.set_active_candidate_profile(
                        tenant_id, str(user_id), updated_profile.profile_id
                    )
                    await self._sender.send_message(
                        chat_id,
                        f"New profile created with negative resume example. Active: {updated_profile.profile_id}",
                    )
            elif mode in ("positive_job", "negative_job"):
                profiles = await self._runner.list_candidate_profiles(tenant_id, str(user_id))
                active_profile_payload = next((p for p in profiles if p["active"]), None)

                if not active_profile_payload:
                    await self._sender.send_message(
                        chat_id, "Error: You must have an active profile to add job examples."
                    )
                    return

                active_profile = await self._runner.get_candidate_profile(
                    tenant_id, str(user_id), active_profile_payload["profile_id"]
                )
                if active_profile:
                    updated_profile = add_example_to_profile(active_profile, text, kind=mode)
                    if self._embedding_provider:
                        updated_profile = await embed_profile_examples(
                            updated_profile, self._embedding_provider
                        )
                    await self._runner.save_candidate_profile(tenant_id, updated_profile)
                    await self._sender.send_message(
                        chat_id, f"Job example added as {mode.replace('_', ' ')} to your profile."
                    )
                else:
                    await self._sender.send_message(
                        chat_id, "Error: Could not load your active profile."
                    )

        except Exception as exc:
            logger.error("upload_failed", mode=mode, error=str(exc), exc_info=True)
            await self._sender.send_message(chat_id, f"Error processing upload: {exc}")

    def _extract_text(self, content: bytes, filename: str) -> str:
        return parse_document(content, filename)

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
                f"Posting enabled for {post_tenant_id} to {channel}. Backend: telegram_posting.",
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
            batch_size = None
            if len(args) >= 3:
                with contextlib.suppress(ValueError):
                    batch_size = int(args[2])
            try:
                await self._runner.update_notify_config(notify_tenant_id, mode, batch_size)
                msg = f"Notification mode for {notify_tenant_id} set to {mode}."
                if batch_size:
                    msg += f" Batch size: {batch_size}."
                await self._sender.send_message(chat_id, msg)
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
                await self._sender.send_message(chat_id, fail_msg)
                return

            added_count = 0
            errors = []
            for link in valid_links:
                try:
                    spec = await build_source_spec_from_input(
                        link,
                        auth_provider=self._runner.get_runtime(tenant_id).auth_provider,
                    )
                    await self._runner.add_source_spec(tenant_id, spec, input_value=link)
                    added_count += 1
                except Exception as exc:
                    errors.append(f"{link}: {exc}")

            total = len(links)
            if not errors:
                await self._sender.send_message(chat_id, f"All {added_count} sources added.")
            else:
                msg = f"Added {added_count}/{total} sources.\nFailed:\n" + "\n".join(errors)
                await self._sender.send_message(chat_id, msg)
            return
        if command == "/run":
            try:
                self._require_admin(user_id)
            except PermissionError as exc:
                await self._sender.send_message(chat_id, f"Access denied: {exc}")
                return
            run_tenant_id: str | None = args[0] if args else None
            if run_tenant_id is None:
                summaries = await self._runner.run_all()
                await self._sender.send_message(chat_id, f"Ran {len(summaries)} tenant(s).")
                return
            summary = await self._runner.run_tenant(run_tenant_id)
            await self._sender.send_message(chat_id, f"{run_tenant_id}: emitted={summary.emitted}")
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
        if command == "/mode":
            valid_modes = ["positive_resume", "negative_resume", "positive_job", "negative_job"]
            if not args or args[0] not in valid_modes:
                await self._sender.send_message(
                    chat_id,
                    f"Usage: /mode <{'|'.join(valid_modes)}>\nCurrent mode: {self._upload_mode.get(user_id, 'positive_resume')}",
                )
                return
            mode = args[0]
            self._upload_mode[user_id] = mode
            await self._sender.send_message(chat_id, f"Upload mode set to: {mode}")
            return
        if command == "/list_examples":
            tenant_id = tenant_ids[0] if tenant_ids else "default"
            profiles = await self._runner.list_candidate_profiles(tenant_id, str(user_id))
            active_profile_payload = next((p for p in profiles if p["active"]), None)
            if not active_profile_payload:
                await self._sender.send_message(chat_id, "No active profile. Upload a resume first.")
                return
            active_profile = await self._runner.get_candidate_profile(
                tenant_id, str(user_id), active_profile_payload["profile_id"]
            )
            if not active_profile or not active_profile.profile.search_profiles:
                await self._sender.send_message(chat_id, "No examples found in active profile.")
                return
            sp = active_profile.profile.search_profiles[0]
            filter_type = args[0] if args else None
            example_lines: list[str] = []
            # Show all or filtered
            show_types: list[str] = (
                [filter_type] if filter_type in ("positive_resume", "negative_resume", "positive_job", "negative_job")
                else ["positive_resume", "negative_resume"]
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
            await self._sender.send_message(chat_id, "\n".join(example_lines) if example_lines else "No examples.")
            return
        if command == "/delete_example":
            if len(args) < 2:
                await self._sender.send_message(
                    chat_id,
                    "Usage: /delete_example <type> <index>\n"
                    "Types: positive_resume, negative_resume, positive_job, negative_job",
                )
                return
            ex_type = args[0]
            valid_types = {"positive_resume", "negative_resume", "positive_job", "negative_job"}
            if ex_type not in valid_types:
                await self._sender.send_message(chat_id, f"Invalid type. Use: {', '.join(sorted(valid_types))}")
                return
            try:
                ex_index = int(args[1])
            except ValueError:
                await self._sender.send_message(chat_id, "Index must be an integer.")
                return
            tenant_id = tenant_ids[0] if tenant_ids else "default"
            profiles = await self._runner.list_candidate_profiles(tenant_id, str(user_id))
            active_profile_payload = next((p for p in profiles if p["active"]), None)
            if not active_profile_payload:
                await self._sender.send_message(chat_id, "No active profile found.")
                return
            active_profile = await self._runner.get_candidate_profile(
                tenant_id, str(user_id), active_profile_payload["profile_id"]
            )
            if not active_profile:
                await self._sender.send_message(chat_id, "Could not load active profile.")
                return
            from job_ftch.adapters.profile_inputs import remove_example_from_profile
            updated_profile = remove_example_from_profile(active_profile, ex_type, ex_index)
            if self._embedding_provider:
                from job_ftch.adapters.profile_inputs import embed_profile_examples
                updated_profile = await embed_profile_examples(updated_profile, self._embedding_provider)
            await self._runner.save_candidate_profile(tenant_id, updated_profile)
            await self._sender.send_message(chat_id, f"Deleted {ex_type}[{ex_index}] from your profile.")
            return
        if command == "/digest":
            digest_tenant_id = args[0] if args else tenant_ids[0]
            jobs = await self._runner.latest_jobs(
                digest_tenant_id,
                limit=self._config.digest_size * 5,  # fetch more for reranking
                user_id=str(user_id),
            )
            # Rerank if reranker available
            if self._reranker and len(jobs) > 1:
                try:
                    # Simple heuristic: use the last part of search profiles or generic query
                    profile_query = "software engineer Python developer"  # TODO: get from profile
                    docs = [f"{j.title} {j.description[:200]}" for j in jobs]
                    scores = await self._reranker.rerank(profile_query, docs)
                    jobs = [j for j, _ in sorted(zip(jobs, scores, strict=False), key=lambda x: x[1], reverse=True)]
                    jobs = jobs[: self._config.digest_size * 3]
                except Exception as exc:
                    logger.warning("reranking_failed", error=str(exc))
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
        if not allowed_users and not allowed_chats and not self._config.open_access:
            return False  # secure by default: deny all if not configured
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
