# Plan: Update Telegram API credentials and create Telethon auth helper

**Goal:** Update root `.env.dev` with new Telegram API credentials from a fresh account,
and create a standalone auth script `scripts/auth_telethon.py` that the user can run
interactively to generate the Telethon session file.

---

## New credentials (from user, my.telegram.org)

- `JOB_FTCH_TELEGRAM_API_ID=35085587`
- `JOB_FTCH_TELEGRAM_API_HASH=42a8ed1bfc73137a177e190e4905fa0f`

---

## Step 1: Update `C:\Users\User\a_projects\job_ftch\.env.dev`

This file is gitignored. Update exactly two lines:

- Find line containing `JOB_FTCH_TELEGRAM_API_ID=` → replace value with `35085587`
- Find line containing `JOB_FTCH_TELEGRAM_API_HASH=` → replace value with `42a8ed1bfc73137a177e190e4905fa0f`

Current values to replace:
```
JOB_FTCH_TELEGRAM_API_ID=35700934
JOB_FTCH_TELEGRAM_API_HASH=0d4aa377434caed075e7c246601b6285 
```

New values:
```
JOB_FTCH_TELEGRAM_API_ID=35085587
JOB_FTCH_TELEGRAM_API_HASH=42a8ed1bfc73137a177e190e4905fa0f
```

---

## Step 2: Ensure `.runtime/` directory exists

Create directory `C:\Users\User\a_projects\job_ftch\.runtime\` if it does not exist.
This is where Telethon will write the session file `telegram-dev.session`.

---

## Step 3: Create `scripts/auth_telethon.py`

Create a new file at `C:\Users\User\a_projects\job_ftch\scripts\auth_telethon.py`.

This is a simple interactive helper script that:
1. Reads API_ID and API_HASH from environment (or uses hardcoded defaults for convenience)
2. Reads session path from `JOB_FTCH_TELEGRAM_SESSION_PATH` env var (default: `.runtime/telegram-dev.session`)
3. Connects to Telegram interactively (prompts for phone + OTP code)
4. Prints confirmation and exits

```python
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

API_ID = int(os.environ.get("JOB_FTCH_TELEGRAM_API_ID", "35085587"))
API_HASH = os.environ.get("JOB_FTCH_TELEGRAM_API_HASH", "42a8ed1bfc73137a177e190e4905fa0f")
SESSION_PATH = os.environ.get("JOB_FTCH_TELEGRAM_SESSION_PATH", ".runtime/telegram-dev.session")


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
```

Note: The script has hardcoded defaults for the new credentials so it works without loading `.env.dev` manually. The user just runs `python scripts/auth_telethon.py`.

---

## Step 4: Ensure `scripts/` is in `.gitignore` considerations

The `scripts/auth_telethon.py` file should be git-tracked (it contains no secrets — credentials are hardcoded as defaults but these are the same as what's already in `.env.dev.example` equivalent). Add it to git.

Actually - the API_ID and API_HASH in the script are real credentials from a fresh secondary account. Consider whether to gitignore this file or keep it generic (reading only from env). For safety, make the hardcoded defaults empty strings and rely purely on environment variables:

```python
API_ID_STR = os.environ.get("JOB_FTCH_TELEGRAM_API_ID", "")
API_HASH = os.environ.get("JOB_FTCH_TELEGRAM_API_HASH", "")

if not API_ID_STR or not API_HASH:
    print("Set JOB_FTCH_TELEGRAM_API_ID and JOB_FTCH_TELEGRAM_API_HASH in environment or .env.dev")
    print("Example: $env:JOB_FTCH_TELEGRAM_API_ID=35085587; python scripts/auth_telethon.py")
    sys.exit(1)

API_ID = int(API_ID_STR)
```

This way the script is safe to commit. The user loads `.env.dev` before running it.

---

## Step 5: Update `adapters/telegram_bot/Dockerfile` — ensure `scripts/` is not included

No change needed (Dockerfile builds from context `.` and installs the package; `scripts/` is not copied unless explicitly included). Verify there is no `COPY scripts /app/scripts` in the Dockerfile.

---

## Verification

1. Confirm `.env.dev` has new API_ID `35085587` and API_HASH `42a8ed1bfc73137a177e190e4905fa0f`.
2. Confirm `scripts/auth_telethon.py` was created.
3. Confirm `.runtime/` directory exists.
4. Run `python -m pytest tests/ -x -q --ignore=scripts` to confirm nothing broken (should still be 589 passed, 10 skipped).
5. Print instructions for the user:
   ```
   Next step: run the auth script to generate the session file:
     # Load env vars first (PowerShell):
     Get-Content .env.dev | ForEach-Object { if ($_ -match '^([^#=]+)=(.*)$') { [System.Environment]::SetEnvironmentVariable($Matches[1], $Matches[2]) } }
     python scripts/auth_telethon.py
   Then:
     docker compose up -d --build
   ```
