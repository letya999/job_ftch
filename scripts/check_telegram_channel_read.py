"""Probe whether a Telegram channel is readable with the current Telethon session.

The script loads root dotenv files itself so it works even when the shell
has not sourced `.env.dev`.

Usage:
    uv run python scripts/check_telegram_channel_read.py
    uv run python scripts/check_telegram_channel_read.py https://t.me/ai_engineers_guild
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

# Force UTF-8 output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]


def _load_project_env() -> None:
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / ".env.dev")


def _read_env(name: str, *, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _channel_ref_from_arg(arg: str) -> str:
    parsed = urlparse(arg)
    if parsed.scheme in {"http", "https"} and parsed.netloc.endswith("t.me"):
        path = parsed.path.strip("/")
        if path:
            return path.split("/")[0]
    return arg.strip()


def _normalize_session_path(value: str) -> Path:
    if not value:
        return ROOT / ".runtime" / "telegram-dev.session"
    return Path(value)


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    message: str


async def _probe_channel(
    *,
    api_id: int,
    api_hash: str,
    session_path: Path,
    channel_ref: str,
    limit: int,
) -> ProbeResult:
    try:
        from telethon import TelegramClient
        from telethon.errors import ChannelPrivateError, FloodWaitError, UsernameNotOccupiedError
    except ImportError:
        return ProbeResult(False, "telethon is not installed")

    session_path.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(session_path), api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            return ProbeResult(False, f"session is not authorized: {session_path}")

        try:
            entity = await client.get_entity(channel_ref)
            messages = await client.get_messages(entity, limit=limit)
        except UsernameNotOccupiedError:
            return ProbeResult(False, f"channel not found: {channel_ref}")
        except ChannelPrivateError:
            return ProbeResult(False, f"channel is private or inaccessible: {channel_ref}")
        except FloodWaitError as exc:
            return ProbeResult(False, f"telegram flood wait: {exc.seconds}s")

        texts = [msg.text for msg in messages if getattr(msg, "text", None)]
        latest_text = texts[0].replace("\n", " ")[:160] if texts else ""
        return ProbeResult(
            True,
            (
                f"readable: yes | messages={len(messages)} | text_messages={len(texts)}"
                + (f" | latest={latest_text}" if latest_text else "")
            ),
        )
    finally:
        await client.disconnect()


async def main() -> int:
    _load_project_env()

    channel_arg = sys.argv[1] if len(sys.argv) > 1 else "https://t.me/ai_engineers_guild"
    channel_ref = _channel_ref_from_arg(channel_arg)
    api_id_raw = _read_env("JOB_FTCH_TELEGRAM_API_ID")
    api_hash = _read_env("JOB_FTCH_TELEGRAM_API_HASH")
    session_path = _normalize_session_path(_read_env("JOB_FTCH_TELEGRAM_SESSION_PATH"))

    if not api_id_raw or not api_hash:
        print("missing JOB_FTCH_TELEGRAM_API_ID or JOB_FTCH_TELEGRAM_API_HASH")
        return 1

    try:
        api_id = int(api_id_raw)
    except ValueError:
        print(f"invalid JOB_FTCH_TELEGRAM_API_ID: {api_id_raw!r}")
        return 1

    print(f"channel: {channel_arg}")
    print(f"resolved: {channel_ref}")
    print(f"session: {session_path}")

    result = await _probe_channel(
        api_id=api_id,
        api_hash=api_hash,
        session_path=session_path,
        channel_ref=channel_ref,
        limit=10,
    )
    print(result.message)
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
