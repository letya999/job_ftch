# Plan: Split adapters/telegram_bot/.env.bot → .env.dev / .env.prod

**Goal:** Replace the single `adapters/telegram_bot/.env.bot` with environment-specific files:
- `adapters/telegram_bot/.env.dev` — local development secrets (gitignored, pre-filled)
- `adapters/telegram_bot/.env.prod` — production secrets (gitignored, empty placeholders)
- `adapters/telegram_bot/.env.dev.example` — tracked template for dev
- `adapters/telegram_bot/.env.prod.example` — tracked template for prod

Also fix docker-compose to use bind mount for `.runtime/` so the Telethon session file
created locally is available inside the container.

---

## Context

- Project root: `C:\Users\User\a_projects\job_ftch`
- Bot adapter: `adapters/telegram_bot/`
- Current bot env file: `adapters/telegram_bot/.env.bot` (gitignored)
- Current bot example: `adapters/telegram_bot/.env.bot.example` (tracked)
- User-provided secrets to fill into `.env.dev`:
  - `JOB_FTCH_AUTH_TELEGRAM_BOT_TOKEN=8882332722:AAGX-m6k4sIJY9SYc7hgtfMSwMcWYRrORwM`
  - `JOB_FTCH_AUTH_TELEGRAM_BOT_ADMIN_USER_IDS=480637186`
  - `JOB_FTCH_AUTH_TELEGRAM_BOT_ALLOWED_USER_IDS=480637186`

---

## Step 1: Create `adapters/telegram_bot/.env.dev`

**Action:** RENAME (copy + delete) `adapters/telegram_bot/.env.bot` → `adapters/telegram_bot/.env.dev`
**Fill in the user's secrets** (replace empty placeholders):

```ini
# ==========================================
# Telegram BOT adapter - DEV environment
# Resolved via EnvAuthProvider (prefix JOB_FTCH_AUTH_TELEGRAM_BOT_).
# Loaded by docker-compose alongside root .env.dev via env_file array.
# ==========================================
# Tenant configs directory. LOCAL: relative path. CONTAINER: /app/adapters/telegram_bot/config/tenants
JOB_FTCH_CONFIGS_DIR=adapters/telegram_bot/config/tenants

# Bot token from @BotFather
JOB_FTCH_AUTH_TELEGRAM_BOT_TOKEN=8882332722:AAGX-m6k4sIJY9SYc7hgtfMSwMcWYRrORwM
# Telegram numeric user IDs. Admins bypass allowlists.
JOB_FTCH_AUTH_TELEGRAM_BOT_ADMIN_USER_IDS=480637186
JOB_FTCH_AUTH_TELEGRAM_BOT_ALLOWED_USER_IDS=480637186
# Optional: restrict to specific chats (comma-separated). Empty = no chat gate.
JOB_FTCH_AUTH_TELEGRAM_BOT_ALLOWED_CHAT_IDS=
# Per-user throttle (seconds) and digest page size.
JOB_FTCH_AUTH_TELEGRAM_BOT_RATE_LIMIT_SECONDS=1.0
JOB_FTCH_AUTH_TELEGRAM_BOT_DIGEST_SIZE=5

# Vector embedder model selection (inherited by tenants)
JOB_FTCH_EMBEDDING_PROVIDER=openai
JOB_FTCH_EMBEDDING_MODEL=text-embedding-3-small
JOB_FTCH_EMBEDDING_DIMENSIONS=1536
```

---

## Step 2: Create `adapters/telegram_bot/.env.prod`

**Action:** CREATE new file (gitignored). Empty secrets, stricter rate limit:

```ini
# ==========================================
# Telegram BOT adapter - PROD environment
# Copy from .env.prod.example and fill in real production values.
# ==========================================
JOB_FTCH_CONFIGS_DIR=adapters/telegram_bot/config/tenants

JOB_FTCH_AUTH_TELEGRAM_BOT_TOKEN=
JOB_FTCH_AUTH_TELEGRAM_BOT_ADMIN_USER_IDS=
JOB_FTCH_AUTH_TELEGRAM_BOT_ALLOWED_USER_IDS=
JOB_FTCH_AUTH_TELEGRAM_BOT_ALLOWED_CHAT_IDS=
JOB_FTCH_AUTH_TELEGRAM_BOT_RATE_LIMIT_SECONDS=2.0
JOB_FTCH_AUTH_TELEGRAM_BOT_DIGEST_SIZE=10

JOB_FTCH_EMBEDDING_PROVIDER=openai
JOB_FTCH_EMBEDDING_MODEL=text-embedding-3-small
JOB_FTCH_EMBEDDING_DIMENSIONS=1536
```

---

## Step 3: Create `adapters/telegram_bot/.env.dev.example`

**Action:** RENAME `adapters/telegram_bot/.env.bot.example` → `adapters/telegram_bot/.env.dev.example`
Content stays the same (placeholder values, no real secrets). Update the header comment to say "DEV environment":

```ini
# ==========================================
# Telegram BOT adapter - DEV environment (example)
# Copy to .env.dev and fill in real values.
# Resolved via EnvAuthProvider (prefix JOB_FTCH_AUTH_TELEGRAM_BOT_).
# ==========================================
JOB_FTCH_CONFIGS_DIR=adapters/telegram_bot/config/tenants

JOB_FTCH_AUTH_TELEGRAM_BOT_TOKEN=123456:ABCdefGhIJKlmNoPQRstuVWxyz
JOB_FTCH_AUTH_TELEGRAM_BOT_ADMIN_USER_IDS=11111111
JOB_FTCH_AUTH_TELEGRAM_BOT_ALLOWED_USER_IDS=11111111
JOB_FTCH_AUTH_TELEGRAM_BOT_ALLOWED_CHAT_IDS=
JOB_FTCH_AUTH_TELEGRAM_BOT_RATE_LIMIT_SECONDS=1.0
JOB_FTCH_AUTH_TELEGRAM_BOT_DIGEST_SIZE=5

JOB_FTCH_EMBEDDING_PROVIDER=openai
JOB_FTCH_EMBEDDING_MODEL=text-embedding-3-small
JOB_FTCH_EMBEDDING_DIMENSIONS=1536
```

---

## Step 4: Create `adapters/telegram_bot/.env.prod.example`

**Action:** CREATE new file (tracked). Same structure as `.env.dev.example` but with prod-appropriate defaults:

```ini
# ==========================================
# Telegram BOT adapter - PROD environment (example)
# Copy to .env.prod and fill in real production values.
# ==========================================
JOB_FTCH_CONFIGS_DIR=adapters/telegram_bot/config/tenants

JOB_FTCH_AUTH_TELEGRAM_BOT_TOKEN=
JOB_FTCH_AUTH_TELEGRAM_BOT_ADMIN_USER_IDS=
JOB_FTCH_AUTH_TELEGRAM_BOT_ALLOWED_USER_IDS=
JOB_FTCH_AUTH_TELEGRAM_BOT_ALLOWED_CHAT_IDS=
JOB_FTCH_AUTH_TELEGRAM_BOT_RATE_LIMIT_SECONDS=2.0
JOB_FTCH_AUTH_TELEGRAM_BOT_DIGEST_SIZE=10

JOB_FTCH_EMBEDDING_PROVIDER=openai
JOB_FTCH_EMBEDDING_MODEL=text-embedding-3-small
JOB_FTCH_EMBEDDING_DIMENSIONS=1536
```

---

## Step 5: Update `docker-compose.yml`

**Action:** MODIFY the `bot` service:

1. Change `env_file` from `adapters/telegram_bot/.env.bot` to `adapters/telegram_bot/.env.dev`
2. Change the `botdata` named volume to a bind mount for `.runtime/` so the locally-created Telethon session file is accessible in the container:

```yaml
services:
  bot:
    build:
      context: .
      dockerfile: adapters/telegram_bot/Dockerfile
    restart: unless-stopped
    env_file:
      - .env.dev
      - adapters/telegram_bot/.env.dev
    environment:
      JOB_FTCH_STORE_DSN: postgresql://job_user:job_password@postgres:5432/job_ftch
      JOB_FTCH_QDRANT_URL: http://qdrant:6333
      JOB_FTCH_CONFIGS_DIR: /app/adapters/telegram_bot/config/tenants
    depends_on:
      postgres:
        condition: service_healthy
      qdrant:
        condition: service_started
    volumes:
      - ./.runtime:/app/.runtime
```

Remove the `botdata` named volume from the `volumes:` section at the bottom of the file.

---

## Step 6: Update `.gitignore`

**Action:** Add/replace `.env.bot` pattern with specific patterns for the new files.

Ensure these lines are in `.gitignore`:
```
adapters/telegram_bot/.env.dev
adapters/telegram_bot/.env.prod
```

Remove (or leave, it's harmless) the old pattern:
```
adapters/telegram_bot/.env.bot
```

Ensure these are NOT in `.gitignore` (they must be tracked):
- `adapters/telegram_bot/.env.dev.example`
- `adapters/telegram_bot/.env.prod.example`

---

## Step 7: Delete old files

- Delete `adapters/telegram_bot/.env.bot` (replaced by `.env.dev`)
- Delete `adapters/telegram_bot/.env.bot.example` (replaced by `.env.dev.example`)

Use `git rm --cached` if they were previously tracked, then `rm` the physical files.
Since `.env.bot` was gitignored (not tracked), just delete it.
Since `.env.bot.example` was tracked, use `git rm adapters/telegram_bot/.env.bot.example` then the new files will be added.

---

## Verification

After all changes:

1. `ls adapters/telegram_bot/` should show `.env.dev`, `.env.dev.example`, `.env.prod`, `.env.prod.example` (not `.env.bot`).
2. `git status` should show `.env.bot.example` deleted, `.env.dev.example` and `.env.prod.example` as new tracked files.
3. `git check-ignore adapters/telegram_bot/.env.dev` should output the file (confirmed gitignored).
4. `git check-ignore adapters/telegram_bot/.env.dev.example` should output nothing (not ignored = tracked).
5. `docker-compose config` should parse without errors.
6. Run full test suite: `python -m pytest tests/ -x -q` — must pass (589 passed, 10 skipped).

---

## What NOT to change

- Root `.env.dev` and `.env.dev.example` — already correct, bot section was removed in previous session.
- `adapters/telegram_bot/config/tenants/ai_jobs.yaml` — leave as-is.
- Any Python source files.
