# Fix All Audit Findings — feature/parsers-phases-23-25

## Context

**Worktree:** `C:/Users/User/a_projects/job_ftch_p2325`  
**Branch:** `feature/parsers-phases-23-25`  
**Working tree is clean** (only `?? .runtime/` untracked — ignore it).

All gates currently pass (boundary, ruff, mypy, pytest 268 passed). Your goal: fix all 12 findings below, keep all gates green, commit atomically by group.

---

## F-001 — CRITICAL | InMemoryStore missing `get_source_strategy`/`save_source_strategy`

**Files to change:** `job_ftch/infrastructure/stores/in_memory.py`

**What to do:**
Add two async methods to `InMemoryStore` that mirror what `sql_adapter.py:142-157` does but use the in-memory dict:
```
async def get_source_strategy(self, domain: str) -> dict[str, str] | None:
    raw = await self.get(f"strategy:{domain}")
    if raw is None:
        return None
    import json
    return json.loads(raw)

async def save_source_strategy(self, domain: str, strategy: dict[str, str]) -> None:
    import json
    await self.set(f"strategy:{domain}", json.dumps(strategy))
```
Use whatever internal `get`/`set` methods already exist on `InMemoryStore`. Do NOT add new dependencies.

**Also fix (F-011 root cause):**
In `job_ftch/application/registry.py`, change the return type of `create_auth_provider` from `object` to `AuthProvider` (import `AuthProvider` from `job_ftch.application.contracts` under `TYPE_CHECKING`).
In `job_ftch/application/tenant_runner.py`, replace all `cast("Any", self._store)` patterns with typed access — the `TenantStore` should declare `self._store: Store` and rely on the protocol, not casts.

**Also add test:**
In `tests/test_phase23_tenants.py`, add a test `test_strategy_roundtrip_memory_backend` that:
1. Creates a `TenantStore` wrapping `InMemoryStore`
2. Calls `save_source_strategy("example.com", {"engine": "playwright"})`
3. Calls `get_source_strategy("example.com")` and asserts `== {"engine": "playwright"}`
4. Calls `get_source_strategy("missing.com")` and asserts `is None`

**Commit message:** `fix(store): implement get/save_source_strategy on InMemoryStore; fix type erasure in registry and TenantStore`

---

## F-002 — HIGH | PermissionError escapes Telegram webhook boundary

**File to change:** `job_ftch/adapters/telegram_bot/bot.py`

**What to do:**
In `handle_command` (the method that calls `_require_admin`), wrap the admin check in a try/except and reply with "Access denied: admin privileges required." to the user:
```python
try:
    self._require_admin(user_id)
except PermissionError as exc:
    await self._send_message(chat_id, f"Access denied: {exc}")
    return
```
Do this for EVERY command that calls `_require_admin` (typically `/run` and `/reset`).

**Also fix in `api.py`:** Wrap `await bot_service.handle_update(payload)` in a try/except that catches `Exception`, logs it, and returns HTTP 200 (so Telegram doesn't retry):
```python
try:
    await bot_service.handle_update(payload)
except Exception as exc:
    logger.error("handle_update_failed", error=str(exc), exc_info=True)
return {"ok": True}
```

**Commit message:** `fix(bot): catch PermissionError in handle_command; swallow unhandled errors at webhook boundary`

---

## F-003 — HIGH | run_all silently swallows tenant failures

**File to change:** `job_ftch/application/tenant_runner.py`

**What to do:**
Find the inner `run_one` coroutine inside `run_all`. Change the bare `except Exception: return None` to:
```python
except Exception as exc:
    logger.error(
        "tenant_run_failed",
        tenant_id=tenant_id,
        error=str(exc),
        exc_info=True,
    )
    return None
```
`logger` is already imported as structlog in the module; use it.

Additionally, consider returning a failed `RunSummary` (with `status="error"` and `error=str(exc)`) instead of `None` so callers can count failures. Only do this if `RunSummary` has optional `status`/`error` fields or if adding them is straightforward without changing existing tests.

**Commit message:** `fix(tenant): log tenant failures in run_all instead of silently returning None`

---

## F-004 — HIGH | File-lock busy-wait without timeout or stale-lock reclaim

**File to change:** `job_ftch/application/tenant_runner.py`

**What to do:**
Find `_tenant_run_lock` (the async context manager using `os.O_EXCL`). Replace the infinite busy-wait loop with:
1. A timeout (e.g. 30 seconds): raise `TimeoutError("Could not acquire run lock for tenant {tenant_id} after 30s")` if not acquired.
2. Stale-lock reclaim: on `FileExistsError`, read the stored PID from the lock file, check if that PID is alive (`os.kill(pid, 0)` raises `ProcessLookupError` if dead), and if the process is dead — remove the lock file and retry immediately.
3. Wrap stale-lock cleanup in try/except (the PID may be reused; if unsure, log a warning and keep waiting).

```python
import time
deadline = time.monotonic() + 30.0
while True:
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        break
    except FileExistsError:
        if time.monotonic() > deadline:
            raise TimeoutError(f"Could not acquire run lock for {tenant_id} after 30s")
        # attempt stale lock reclaim
        try:
            pid_bytes = lock_path.read_bytes()
            stale_pid = int(pid_bytes.strip())
            os.kill(stale_pid, 0)  # raises if dead
        except (ProcessLookupError, ValueError, OSError):
            logger.warning("reclaiming_stale_lock", tenant_id=tenant_id)
            lock_path.unlink(missing_ok=True)
            continue
        await asyncio.sleep(0.1)
```

**Commit message:** `fix(tenant): add timeout and stale-lock reclaim to _tenant_run_lock`

---

## F-005 — MEDIUM | Cross-tenant data exposure + unbounded limit

**Files to change:**
- `job_ftch/adapters/mcp/server.py`
- `job_ftch/adapters/telegram_bot/api.py`
- `job_ftch/application/tenant_runner.py` (the cross-tenant search method)

**What to do:**

1. **Clamp `limit`** in all search entry points: `limit = min(limit, 100)` before passing to backend.

2. **Require tenant_id** for cross-tenant queries — if `tenant_id` is None and there is more than one tenant, either:
   - Raise `ValueError("tenant_id is required when multiple tenants are configured")`
   - OR if a "default" single-tenant mode is acceptable, allow None only when `len(tenant_ids) == 1`

3. **Add auth to `/jobs/search`** in `api.py`: apply the same `bridge_api_key` check used by `/pipeline/run` — check the `X-API-Key` header and return HTTP 403 if it doesn't match `config.bridge_api_key` (skip check only if `bridge_api_key` is not configured).

**Commit message:** `fix(security): clamp search limit, require tenant_id for multi-tenant, auth on /jobs/search`

---

## F-006 — MEDIUM | Fail-open webhook auth; non-constant-time comparison

**File to change:** `job_ftch/adapters/telegram_bot/api.py`

**What to do:**

1. **Fail closed:** If `bot_config.secret_token` is None/empty, refuse to start: raise `RuntimeError("TELEGRAM_SECRET_TOKEN must be set; refusing to start without authentication")` in the `create_app` factory or in the startup event.

2. **Use `hmac.compare_digest`** for all token comparisons:
```python
import hmac
if not hmac.compare_digest(received_token or "", expected_token):
    raise HTTPException(status_code=403, detail="Invalid secret token")
```
Do the same for `bridge_api_key` in `api.py`.

**Commit message:** `fix(security): fail-closed on missing secret_token; use hmac.compare_digest for token checks`

---

## F-007 — MEDIUM | RunSummary deserialization without error handling

**File to change:** `job_ftch/application/tenant_runner.py`

**What to do:**
Find `get_status` (the method that does `json.loads(raw)` then `RunSummary(**payload)`). Wrap the entire block in try/except:
```python
try:
    payload = json.loads(raw)
    return RunSummary(**payload)
except (json.JSONDecodeError, TypeError, ValueError) as exc:
    logger.warning("status_decode_failed", tenant_id=tenant_id, error=str(exc))
    return None
```
If `RunSummary` is a pydantic model, prefer `RunSummary.model_validate(payload)` (tolerates extra fields).

**Commit message:** `fix(tenant): guard RunSummary deserialization against schema drift and corrupt state`

---

## F-008 — MEDIUM | Phase 25 has no CLI entry point; extras mismatch

**Files to change:**
- `job_ftch/cli.py` — add a `telegram-bot` subcommand
- `pyproject.toml` — fix the `bot` extra to list actual dependencies

**What to do:**

1. **Add CLI subcommand** in `cli.py`. Look at how the existing `mcp-server` subcommand is structured and add a similar `telegram-bot` command:
```
job-ftch telegram-bot [--polling | --webhook]
  --polling: call run_polling_loop() from adapters.telegram_bot.bot
  --webhook: call uvicorn.run(create_app(...), host=..., port=...)
```
Import the bot modules lazily (inside the command function) with a try/except ImportError that prints "Install job-ftch[bot] to use the Telegram bot" and exits.

2. **Fix pyproject.toml extras:**
   - Look at what `adapters/telegram_bot/api.py` actually imports at the top level (likely `fastapi`, `uvicorn`, `httpx`)
   - Look at what `adapters/telegram_bot/bot.py` actually imports
   - Change the `bot` extra to match the actual imports (NOT `aiogram` unless it's actually used)
   - If `fastapi`/`uvicorn` are in both `api` and `bot` extras, that's fine — list them in both

**Commit message:** `feat(cli): add telegram-bot subcommand; fix bot extras to match actual imports`

---

## F-009 — MEDIUM | Phase 24/25 tests fake entire frameworks

**Files to change:**
- `tests/test_phase24_mcp_server.py`
- `tests/test_phase25_telegram_bot.py`

**What to do:**
Add REAL integration tests alongside the existing fake-framework unit tests (keep existing tests, add new ones).

For Phase 24 (MCP):
- Add a test gated with `pytest.importorskip("fastmcp")` that imports the real `FastMCP` and calls the tool handlers directly (not via monkeypatching the module).
- Test that `search_jobs` returns a list, `get_job` returns a job or None, `list_sources` returns a list.

For Phase 25 (Telegram bot + FastAPI):
- Add a test gated with `pytest.importorskip("fastapi")` that creates the real FastAPI app via `create_app(...)` and drives it with `from fastapi.testclient import TestClient`.
- Test the webhook endpoint: POST a fake Telegram update with correct and incorrect secret tokens; expect 200 and 403 respectively.
- Test the `/jobs/search` endpoint auth (after F-005 fix): expect 403 without API key.

Use `MagicMock` or simple stubs ONLY for the Telegram HTTP client and the pipeline runner — not for the web framework itself.

**Commit message:** `test: add real-framework integration tests for MCP server and Telegram bot`

---

## F-010 — LOW | Blocking syscalls in async context

**File to change:** `job_ftch/application/tenant_runner.py`

**What to do:**
In `_tenant_run_lock`, wrap the blocking filesystem calls in `asyncio.to_thread`:
```python
import asyncio

def _acquire_lock_sync(lock_path, tenant_id, deadline):
    # move all the os.open / os.write / os.close / unlink blocking logic here
    ...

await asyncio.to_thread(_acquire_lock_sync, lock_path, tenant_id, deadline)
```
The `mkdir` call for the lock directory can also be moved into the sync function or left as-is (it's a one-time call).

If refactoring `_tenant_run_lock` into sync+async is complex, at minimum wrap `mkdir` and `os.open` in `asyncio.to_thread(lambda: ...)`.

**Commit message:** `perf(tenant): move blocking lock syscalls off the event loop with asyncio.to_thread`

---

## F-011 — LOW | Type erasure in registry and TenantStore

*(Already handled as part of F-001 fix above — covered in the same commit)*

---

## F-012 — LOW | Telegram formatter unbounded field lengths

**File to change:** `job_ftch/adapters/telegram_bot/formatter.py`

**What to do:**
After HTML-escaping each field, truncate to safe lengths:
```python
MAX_TITLE = 200
MAX_COMPANY = 100
MAX_LOCATION = 100
MAX_URL = 500
MAX_DESCRIPTION = 280
MAX_TOTAL = 3800  # leave headroom below Telegram's 4096

title = escape(job.title or "Untitled role")[:MAX_TITLE]
company = escape(job.company or "Unknown")[:MAX_COMPANY]
location = escape(job.location or "")[:MAX_LOCATION]
url = (job.url or "")[:MAX_URL]
description = escape((job.description or "")[:MAX_DESCRIPTION].strip())
```
After assembling the full message, also hard-cap: `message = message[:MAX_TOTAL]`.

**Commit message:** `fix(bot): bound field lengths in formatter to stay within Telegram's 4096-char limit`

---

## Quality Gates (run after ALL fixes, in the worktree)

```bash
cd C:/Users/User/a_projects/job_ftch_p2325
uv run python scripts/check_module_boundaries.py
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest tests/ -q
```

If `ruff format --check` fails: `uv run ruff format . && git add -u && git commit -m "style: ruff format after audit fixes"`

All gates must be green before finishing. Target: 268+ tests passed (new tests from F-001 and F-009 should push this higher).

## Commit order

Commit each fix group separately in this order:
1. F-001 + F-011: `fix(store): implement get/save_source_strategy on InMemoryStore; fix type erasure`
2. F-002: `fix(bot): catch PermissionError; swallow errors at webhook boundary`
3. F-003: `fix(tenant): log failures in run_all`
4. F-004: `fix(tenant): timeout and stale-lock reclaim in _tenant_run_lock`
5. F-005: `fix(security): clamp limit, require tenant_id, auth on /jobs/search`
6. F-006: `fix(security): fail-closed on missing secret_token; hmac.compare_digest`
7. F-007: `fix(tenant): guard RunSummary deserialization`
8. F-008: `feat(cli): telegram-bot subcommand; fix bot extras`
9. F-009: `test: real-framework integration tests for MCP and bot`
10. F-010: `perf(tenant): asyncio.to_thread for lock syscalls`
11. F-012: `fix(bot): bound field lengths in formatter`

Do NOT merge to main. Do NOT push. Working tree must be clean at end.

## Flow
Use `claude_exec` flow.
