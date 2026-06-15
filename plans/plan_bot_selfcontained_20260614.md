# Plan: Make Telegram bot adapter self-contained

**Goal:** Everything Telegram-bot-specific (its own env vars, tenant configs) must live inside `adapters/telegram_bot/`. The root project is untouched by bot concerns.

---

## Context

- Project root: `C:\Users\User\a_projects\job_ftch`
- Bot adapter: `adapters/telegram_bot/`
- Build context for Docker is root `.` (Dockerfile needs access to `job_ftch/` lib package)
- `docker-compose.yml` is at root (gitignored)
- Tenant configs currently at `config/tenants/ai_jobs.yaml` (created last session)
- Bot-specific env vars currently mixed into root `.env.dev` (lines 36-59)

---

## Files to Create

### 1. `adapters/telegram_bot/.env.bot`
**Gitignored.** Contains bot-specific secrets and config extracted from root `.env.dev`.

```ini
# ==========================================
# Telegram BOT adapter (aiogram) - required to start `job_ftch telegram-bot`
# Resolved via EnvAuthProvider from the process env (prefix JOB_FTCH_AUTH_TELEGRAM_BOT_).
# In docker compose these are injected from this file via env_file.
# ==========================================
# Tenant configs directory consumed by the bot (TenantRunner).
# LOCAL: relative path from project root. CONTAINER: /app/adapters/telegram_bot/config/tenants
JOB_FTCH_CONFIGS_DIR=adapters/telegram_bot/config/tenants

# Bot token from @BotFather (REQUIRED - fill this in).
JOB_FTCH_AUTH_TELEGRAM_BOT_TOKEN=
# Telegram numeric user IDs. Admins bypass allowlists; allowed users may use the bot.
JOB_FTCH_AUTH_TELEGRAM_BOT_ADMIN_USER_IDS=
JOB_FTCH_AUTH_TELEGRAM_BOT_ALLOWED_USER_IDS=
# Optional: restrict to specific chats (comma-separated chat IDs). Empty = no chat gate.
JOB_FTCH_AUTH_TELEGRAM_BOT_ALLOWED_CHAT_IDS=
# Per-user throttle (seconds) and digest page size.
JOB_FTCH_AUTH_TELEGRAM_BOT_RATE_LIMIT_SECONDS=1.0
JOB_FTCH_AUTH_TELEGRAM_BOT_DIGEST_SIZE=5

# Vector embedder model selection (inherited by tenants)
JOB_FTCH_EMBEDDING_PROVIDER=openai
JOB_FTCH_EMBEDDING_MODEL=text-embedding-3-small
JOB_FTCH_EMBEDDING_DIMENSIONS=1536
```

### 2. `adapters/telegram_bot/.env.bot.example`
**Git-tracked.** Same content as `.env.bot` but with placeholder values (no real secrets). Use the same placeholder style as root `.env.dev.example`.

```ini
# (same structure as .env.bot but with dummy values)
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

### 3. `adapters/telegram_bot/config/tenants/ai_jobs.yaml`
**Move** (not copy) from `config/tenants/ai_jobs.yaml`. Keep content identical. This directory (`adapters/telegram_bot/config/`) is git-tracked.

---

## Files to Modify

### 4. Root `.env.dev` (gitignored)
**Remove** the bot-specific section (lines starting from the `# Telegram BOT adapter` comment through to the end of embedding vars at line 59). The root `.env.dev` should remain focused on core pipeline vars (source/sink/store/openai/qdrant/telegram-API credentials).

The removed vars are:
```
JOB_FTCH_CONFIGS_DIR=config/tenants
JOB_FTCH_AUTH_TELEGRAM_BOT_TOKEN=
JOB_FTCH_AUTH_TELEGRAM_BOT_ADMIN_USER_IDS=
JOB_FTCH_AUTH_TELEGRAM_BOT_ALLOWED_USER_IDS=
JOB_FTCH_AUTH_TELEGRAM_BOT_ALLOWED_CHAT_IDS=
JOB_FTCH_AUTH_TELEGRAM_BOT_RATE_LIMIT_SECONDS=1.0
JOB_FTCH_AUTH_TELEGRAM_BOT_DIGEST_SIZE=5
JOB_FTCH_EMBEDDING_PROVIDER=openai
JOB_FTCH_EMBEDDING_MODEL=text-embedding-3-small
JOB_FTCH_EMBEDDING_DIMENSIONS=1536
```

### 5. Root `.env.dev.example` (git-tracked)
Same removals as `.env.dev`: strip the bot-specific section (from `# Telegram BOT adapter` block through embedding vars). The example should instruct users to see `adapters/telegram_bot/.env.bot.example` for bot config. Add a comment near the bottom:

```ini
# ==========================================
# Telegram BOT adapter configuration
# See adapters/telegram_bot/.env.bot.example for bot-specific vars.
# Copy it to adapters/telegram_bot/.env.bot and fill in secrets.
# ==========================================
```

### 6. `docker-compose.yml` (gitignored, at project root)
Update the `bot` service to load two env files (compose v3 supports an array):

```yaml
bot:
  build:
    context: .
    dockerfile: adapters/telegram_bot/Dockerfile
  restart: unless-stopped
  env_file:
    - .env.dev
    - adapters/telegram_bot/.env.bot
  environment:
    # Override localhost values with in-network service hostnames.
    JOB_FTCH_STORE_DSN: postgresql://job_user:job_password@postgres:5432/job_ftch
    JOB_FTCH_QDRANT_URL: http://qdrant:6333
    JOB_FTCH_CONFIGS_DIR: /app/adapters/telegram_bot/config/tenants
  depends_on:
    postgres:
      condition: service_healthy
    qdrant:
      condition: service_started
  volumes:
    - botdata:/app/.runtime
```

### 7. `adapters/telegram_bot/Dockerfile`
Add a COPY instruction to include the tenant config directory in the image (build context is root `.`):

After the `COPY . .` (or equivalent), add:
```dockerfile
# Tenant configs live inside the adapter directory
COPY adapters/telegram_bot/config /app/adapters/telegram_bot/config
```

If there's already a `COPY . .` that copies everything, this is redundant — verify. If COPY is selective (only copies specific dirs), add this line explicitly.

### 8. `.gitignore` (project root)
Ensure these patterns are present:
```
adapters/telegram_bot/.env.bot
```
And ensure `.env.bot.example` is NOT ignored (it must be tracked).

---

## Files to Delete

### 9. `config/tenants/ai_jobs.yaml`
After moving to `adapters/telegram_bot/config/tenants/ai_jobs.yaml`, delete the original. If `config/tenants/` is now empty, the directory can also be removed (verify nothing else uses it).

---

## Verification Steps

After all changes:

1. Check that `config/tenants/` no longer exists (or is empty and unused).
2. Confirm `adapters/telegram_bot/config/tenants/ai_jobs.yaml` exists.
3. Confirm `adapters/telegram_bot/.env.bot` exists (even with empty TOKEN).
4. Confirm `adapters/telegram_bot/.env.bot.example` is git-tracked.
5. Run: `python -c "from job_ftch.application.builder import tenant_to_settings; print('ok')"` — should not error.
6. Run full test suite: `python -m pytest tests/ -x -q` — must still be 588 passed, 10 skipped.
7. Confirm `.gitignore` ignores `adapters/telegram_bot/.env.bot` but tracks `.env.bot.example`.

---

## What NOT to Change

- `job_ftch/cli.py` — `_run_telegram_bot` reads `JOB_FTCH_CONFIGS_DIR` from the environment; it doesn't hardcode paths. No changes needed.
- `adapters/telegram_bot/config.py` — reads via `EnvAuthProvider`, no path changes needed.
- `job_ftch/application/builder.py` — `tenant_to_settings` fix from previous session stays as-is.
- Any files in `config/` root (non-tenant yaml configs like `sources.example.yaml`, `company_aliases.yaml`) — those are pipeline configs, not bot configs. Leave them in place.

---

## Flow

Use the default flow from `flow.config.json`.
