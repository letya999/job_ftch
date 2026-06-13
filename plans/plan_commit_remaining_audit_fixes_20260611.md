# Commit remaining audit fixes — feature/parsers-phases-23-25

## Context

**Worktree:** `C:/Users/User/a_projects/job_ftch_p2325`
**Branch:** `feature/parsers-phases-23-25`

Gemini already committed F-001/F-011 as `77dfe4c`. The following 8 files are modified but NOT yet staged/committed:

- `job_ftch/adapters/mcp/server.py` — F-005 fixes (limit clamp, tenant_id required)
- `job_ftch/adapters/telegram_bot/api.py` — F-002, F-005, F-006 fixes
- `job_ftch/adapters/telegram_bot/bot.py` — F-002 fix (PermissionError catch)
- `job_ftch/adapters/telegram_bot/formatter.py` — F-012 fix (field length bounds)
- `job_ftch/cli.py` — F-008 fix (telegram-bot subcommand)
- `pyproject.toml` — F-008 fix (extras correction)
- `tests/test_phase24_mcp_server.py` — F-009 fix (real framework tests)
- `tests/test_phase25_telegram_bot.py` — F-009 fix (real framework tests)

**All quality gates are currently passing:** boundary OK, ruff OK, 269 tests passed.

## Steps

### Step 1: Verify no ruff format issues
```bash
cd C:/Users/User/a_projects/job_ftch_p2325
uv run ruff format --check .
```
If it fails: `uv run ruff format . && git add -u`

### Step 2: Commit security + bot fixes together
```bash
git add job_ftch/adapters/telegram_bot/bot.py job_ftch/adapters/telegram_bot/api.py job_ftch/adapters/mcp/server.py
git commit -m "fix(security): catch PermissionError at webhook boundary; clamp search limit; auth on /jobs/search; hmac.compare_digest for tokens; fail-closed on missing secret_token"
```

### Step 3: Commit formatter fix
```bash
git add job_ftch/adapters/telegram_bot/formatter.py
git commit -m "fix(bot): bound field lengths in formatter to stay within Telegram 4096-char limit"
```

### Step 4: Commit CLI + extras fix
```bash
git add job_ftch/cli.py pyproject.toml
git commit -m "feat(cli): add telegram-bot subcommand; fix bot extras to match actual imports"
```

### Step 5: Commit test improvements
```bash
git add tests/test_phase24_mcp_server.py tests/test_phase25_telegram_bot.py
git commit -m "test: add real-framework integration tests for MCP server and Telegram bot"
```

### Step 6: Run all gates
```bash
uv run python scripts/check_module_boundaries.py
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest tests/ -q
```

All must pass. If mypy fails on new code — fix it inline and amend/commit `fix(types): ...`.
If pytest fails — fix and commit `fix: ...`.

### Step 7: Also check F-003, F-004, F-007 were addressed
Check `job_ftch/application/tenant_runner.py` for:
- F-003: `run_all` now logs failures (not bare `except Exception: return None`)
- F-004: `_tenant_run_lock` has timeout + stale-lock reclaim
- F-007: `get_status` has try/except around `json.loads`/`RunSummary()`

If ANY of these are still missing in the file — implement them now and commit:
```bash
git add job_ftch/application/tenant_runner.py
git commit -m "fix(tenant): log run_all failures; timeout + stale-lock reclaim; guard RunSummary deserialization"
```

### Step 8: Final state
```bash
git log --oneline -12
git status --short
```
Working tree must be clean (only `?? .runtime/`).

## Flow
Use `claude_exec` flow (Gemini output capture is unreliable for status, but changes land).
