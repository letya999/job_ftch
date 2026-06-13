# Commit final 5 audit-fix files

## Context

**Worktree:** `C:/Users/User/a_projects/job_ftch_p2325`
**Branch:** `feature/parsers-phases-23-25`

F-001 through F-007 are already committed. 5 files remain modified but uncommitted:
- `job_ftch/adapters/telegram_bot/formatter.py` — F-012 (field length bounds)
- `job_ftch/cli.py` — F-008 (telegram-bot subcommand)
- `pyproject.toml` — F-008 (extras correction)
- `tests/test_phase24_mcp_server.py` — F-009 (real framework tests)
- `tests/test_phase25_telegram_bot.py` — F-009 (real framework tests)

## Steps

### Step 1: Format check
```bash
cd C:/Users/User/a_projects/job_ftch_p2325
uv run ruff format --check .
```
If fails: `uv run ruff format . `

### Step 2: Commit formatter fix (F-012)
```bash
git add job_ftch/adapters/telegram_bot/formatter.py
git commit -m "fix(bot): bound field lengths in formatter to stay within Telegram 4096-char limit"
```

### Step 3: Commit CLI + extras (F-008)
```bash
git add job_ftch/cli.py pyproject.toml
git commit -m "feat(cli): add telegram-bot subcommand; fix bot extras to match actual imports"
```

### Step 4: Commit test improvements (F-009)
```bash
git add tests/test_phase24_mcp_server.py tests/test_phase25_telegram_bot.py
git commit -m "test: add real-framework integration tests for MCP server and Telegram bot"
```

### Step 5: Run all gates
```bash
uv run python scripts/check_module_boundaries.py
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest tests/ -q
```

All must pass. Fix any failures and commit `fix: ...` before finishing.

### Step 6: Final log
```bash
git log --oneline -10
git status --short
```
Working tree must be clean (only `?? .runtime/`).
