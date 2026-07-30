"""
One-time Telethon authentication helper.
Run this once to create or refresh .runtime/telegram-dev.session before
starting Docker.

The script loads root dotenv files itself so it works even when the shell
has not sourced .env.dev.

Usage:
    python scripts/auth_telethon.py
    python scripts/auth_telethon.py --reset-session
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import webbrowser
from contextlib import suppress
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
ENV_FILES = (ROOT / ".env", ROOT / ".env.dev")
ORIGINAL_ENV_KEYS = set(os.environ.keys())


def _load_env_file(path: Path, *, override: bool) -> None:
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        if key in ORIGINAL_ENV_KEYS:
            continue
        if not override and key in os.environ:
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


def _load_project_env() -> None:
    _load_env_file(ENV_FILES[0], override=False)
    _load_env_file(ENV_FILES[1], override=True)


def _read_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    return value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telethon session auth helper")
    parser.add_argument(
        "--reset-session",
        action="store_true",
        help="Back up the current session file and force a fresh login",
    )
    parser.add_argument(
        "--request-only",
        action="store_true",
        help="Request a login code and print Telethon delivery diagnostics without signing in",
    )
    parser.add_argument(
        "--qr-login",
        action="store_true",
        help="Authorize the session by scanning a QR code from an already logged-in Telegram client",
    )
    return parser.parse_args()


def _backup_session_file(session_path: Path) -> Path | None:
    if not session_path.exists():
        return None

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = session_path.with_name(f"{session_path.name}.bak-{stamp}")
    session_path.replace(backup_path)
    return backup_path


_load_project_env()

API_ID_STR = _read_env("JOB_FTCH_TELEGRAM_API_ID")
API_HASH = _read_env("JOB_FTCH_TELEGRAM_API_HASH")
SESSION_PATH = _read_env("JOB_FTCH_TELEGRAM_SESSION_PATH") or ".runtime/telegram-dev.session"

if not API_ID_STR or not API_HASH:
    print("Set JOB_FTCH_TELEGRAM_API_ID and JOB_FTCH_TELEGRAM_API_HASH in .env.dev or shell env.")
    print("The helper now auto-loads root .env and .env.dev, so usually no export is needed.")
    sys.exit(1)

API_ID = int(API_ID_STR)
QR_HTML_PATH = ROOT / ".runtime" / "telegram-login-qr.html"


def _phone_prompt() -> str:
    return input("Phone number (+country...): ").strip()


def _code_prompt() -> str:
    return input("Telegram code: ").strip()


def _password_prompt() -> str:
    password = input("2FA password (if enabled): ").strip()
    if not password:
        raise RuntimeError("2FA password is required for this login flow.")
    return password


def _sent_code_field(sent: Any, name: str) -> str:
    value = getattr(sent, name, None)
    if value is None:
        return "-"
    return str(value)


def _print_sent_code_diagnostics(sent: Any) -> None:
    sent_type = getattr(sent, "type", None)
    next_type = getattr(sent, "next_type", None)
    timeout = getattr(sent, "timeout", None)
    print("Code request accepted by Telegram.")
    print(f"Delivery type: {sent_type!s}")
    print(
        f"Next delivery type: {next_type!s}" if next_type is not None else "Next delivery type: -"
    )
    print(f"Timeout hint: {timeout!s}" if timeout is not None else "Timeout hint: -")
    print(f"Phone code hash present: {'yes' if getattr(sent, 'phone_code_hash', None) else 'no'}")


def _write_qr_html(url: str, *, expires: object) -> Path:
    encoded_url = quote(url, safe="")
    qr_image_url = f"https://quickchart.io/qr?text={encoded_url}&size=320"
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Telegram QR Login</title>
  <style>
    body {{
      font-family: Arial, sans-serif;
      background: #f5f7fb;
      color: #111827;
      margin: 0;
      padding: 32px;
    }}
    main {{
      max-width: 760px;
      margin: 0 auto;
      background: #ffffff;
      border-radius: 16px;
      padding: 24px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.08);
    }}
    img {{
      display: block;
      width: 320px;
      height: 320px;
      margin: 16px auto;
      border: 1px solid #d1d5db;
      border-radius: 12px;
      background: #fff;
    }}
    code {{
      display: block;
      overflow-wrap: anywhere;
      background: #f3f4f6;
      padding: 12px;
      border-radius: 8px;
    }}
  </style>
</head>
<body>
  <main>
    <h1>Telegram QR Login</h1>
    <p>Open Telegram on an already logged-in phone, then scan this QR code.</p>
    <p>Expires: {escape(str(expires))}</p>
    <img src="{qr_image_url}" alt="Telegram login QR code">
    <p>If the image does not load, open this link in a browser that can render the QR service:</p>
    <code>{escape(qr_image_url)}</code>
    <p>Raw Telegram login URL:</p>
    <code>{escape(url)}</code>
  </main>
</body>
</html>
"""
    QR_HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    QR_HTML_PATH.write_text(html, encoding="utf-8")
    return QR_HTML_PATH


async def _authorize_via_qr(client: Any) -> bool:
    authorized = await client.is_user_authorized()
    if authorized:
        return False

    qr_login = await client.qr_login()
    qr_html_path = _write_qr_html(qr_login.url, expires=qr_login.expires)
    print("Open Telegram on a logged-in phone and scan the QR page:")
    print(qr_html_path)
    print(f"QR expires at: {qr_login.expires}")
    with suppress(Exception):
        webbrowser.open(qr_html_path.resolve().as_uri())

    try:
        await qr_login.wait()
    except Exception as exc:
        if exc.__class__.__name__ == "SessionPasswordNeededError":
            try:
                await client.sign_in(password=_password_prompt())
            except Exception as password_exc:
                if password_exc.__class__.__name__ == "PasswordHashInvalidError":
                    raise RuntimeError(
                        "Telegram rejected the 2FA password as invalid."
                    ) from password_exc
                raise
        else:
            raise

    return True


async def _ensure_session(client: Any) -> bool:
    authorized = await client.is_user_authorized()
    if authorized:
        return False

    print("Existing session is not authorized. Refreshing it now.")

    phone = _phone_prompt()
    sent = await client.send_code_request(phone)
    _print_sent_code_diagnostics(sent)
    phone_code_hash = sent.phone_code_hash

    for attempt in range(1, 4):
        try:
            code = _code_prompt()
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
            return True
        except Exception as exc:
            error_name = exc.__class__.__name__
            if error_name == "SessionPasswordNeededError":
                await client.sign_in(password=_password_prompt())
                return True
            if error_name == "PhoneCodeInvalidError":
                print("Invalid code. Requesting a fresh one.")
            elif error_name == "PhoneCodeExpiredError":
                print("Code expired. Requesting a fresh one.")
            else:
                raise

        if attempt < 3:
            sent = await client.send_code_request(phone)
            _print_sent_code_diagnostics(sent)
            phone_code_hash = sent.phone_code_hash

    raise RuntimeError("Failed to authenticate after 3 code attempts.")


async def main() -> None:
    args = _parse_args()
    try:
        from telethon import TelegramClient
    except ImportError:
        print("telethon not installed. Run: pip install telethon")
        sys.exit(1)

    session_path = Path(SESSION_PATH)
    session_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Using project env files: {', '.join(str(path) for path in ENV_FILES)}")
    print(f"Session path: {session_path}")
    print(f"Using api_id={API_ID}")

    if args.reset_session:
        backup_path = _backup_session_file(session_path)
        if backup_path is not None:
            print(f"Backed up previous session to: {backup_path}")
        else:
            print("No existing session file to back up.")

    client = TelegramClient(str(session_path), API_ID, API_HASH)
    await client.connect()
    try:
        if args.qr_login:
            refreshed = await _authorize_via_qr(client)
            me = await client.get_me()
        elif args.request_only:
            phone = _phone_prompt()
            sent = await client.send_code_request(phone)
            _print_sent_code_diagnostics(sent)
            print("Request-only mode: no sign-in attempted.")
            return
        else:
            refreshed = await _ensure_session(client)
            me = await client.get_me()
    finally:
        await client.disconnect()

    if refreshed:
        print(f"\nSession refreshed and saved to: {session_path}")
    else:
        print(f"\nSession already valid at: {session_path}")

    if me is None:
        print("Telegram did not return account information.")
        sys.exit(1)

    username = f"@{me.username}" if getattr(me, "username", None) else "(no username)"
    print(f"Authenticated as: {me.first_name} (id={me.id}, {username})")
    print("\nDone. You can now run: docker compose up -d --build")


if __name__ == "__main__":
    asyncio.run(main())
