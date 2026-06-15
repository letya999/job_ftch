"""
One-time Telethon authentication helper.
Run this once to create .runtime/telegram-dev.session before starting Docker.

Usage:
    python scripts/auth_telethon.py
"""
import asyncio
import os
import sys
from pathlib import Path

API_ID_STR = os.environ.get("JOB_FTCH_TELEGRAM_API_ID", "")
API_HASH = os.environ.get("JOB_FTCH_TELEGRAM_API_HASH", "")
SESSION_PATH = os.environ.get("JOB_FTCH_TELEGRAM_SESSION_PATH", ".runtime/telegram-dev.session")

if not API_ID_STR or not API_HASH:
    print("Set JOB_FTCH_TELEGRAM_API_ID and JOB_FTCH_TELEGRAM_API_HASH in environment or .env.dev")
    print("Example: $env:JOB_FTCH_TELEGRAM_API_ID=35085587; python scripts/auth_telethon.py")
    sys.exit(1)

API_ID = int(API_ID_STR)


async def main() -> None:
    try:
        from telethon import TelegramClient
    except ImportError:
        print("telethon not installed. Run: pip install telethon")
        sys.exit(1)

    Path(SESSION_PATH).parent.mkdir(parents=True, exist_ok=True)

    print(f"Creating Telethon session at: {SESSION_PATH}")
    print(f"Using api_id={API_ID}")
    print()

    async with TelegramClient(SESSION_PATH, API_ID, API_HASH) as client:
        me = await client.get_me()
        print(f"\nAuthenticated as: {me.first_name} (id={me.id}, @{me.username})")
        print(f"Session saved to: {SESSION_PATH}")
        print("\nDone. You can now run: docker compose up -d --build")


if __name__ == "__main__":
    asyncio.run(main())
