# Plan: MVP Bot Batch 1 — Access Hardening + Job Examples + Source Validation + Auto-Scheduler

## Goal
Make the Telegram bot MVP fully operational:
1. Bot securely blocks unauthorized users (currently open if allowed_user_ids is empty)
2. User can upload positive/negative resume AND job examples via bot
3. Sources are validated (reachable) before being added
4. Bot auto-polls all tenants every 4 hours in background alongside its polling loop

## Architecture constraints (MUST follow, from AGENTS.md + docs/architecture.md)
- `domain/` zero imports outside pydantic + stdlib. ONLY add pydantic-compatible fields (tuples of str/float).
- New files in `infrastructure/` or `adapters/` can import freely.
- Layer boundary: `nodes/`, `sinks/` can only import from `domain/` + `application/`, NOT from `infrastructure/`.
- `SanitizeNode` always first — do NOT touch node ordering for that.
- No hardcoded if/elif dispatch in core — adapters self-register.
- Commits: feat, fix, chore, docs, refactor only. NO Co-authored-by. NO AI attribution.
- All tests must pass: `python -m pytest tests/ -x -q`
- Ruff must be clean: `python -m ruff check job_ftch/`
- Mypy must be clean: `python -m mypy job_ftch/`

---

## BLOCK 1: Access Control Hardening

### Problem
`TelegramBotService._is_allowed()` in `job_ftch/adapters/telegram_bot/bot.py:581` returns `True`
when `allowed_user_ids` is empty (`not allowed_users or user_id in allowed_users`). This means
an unconfigured bot is open to everyone.

### Fix
Modify `bot.py` `_is_allowed()` to check for a new flag `open_access` in `TelegramBotConfig`:
- Add `open_access: bool = False` field to `TelegramBotConfig` dataclass (line ~41)
- In `load_bot_config()` (line ~53): set `open_access=payload.get("open_access", "false").lower() == "true"`
  - Env var: `JOB_FTCH_AUTH_TELEGRAM_BOT_OPEN_ACCESS=true` (only for dev/testing)
- Change `_is_allowed()` logic:
  ```python
  def _is_allowed(self, *, user_id: int, chat_id: int) -> bool:
      allowed_users = self._config.allowed_user_ids
      allowed_chats = self._config.allowed_chat_ids
      if not allowed_users and not allowed_chats and not self._config.open_access:
          return False  # secure by default: deny all if not configured
      user_ok = not allowed_users or user_id in allowed_users
      chat_ok = not allowed_chats or chat_id in allowed_chats
      return user_ok and chat_ok
  ```
- Also: `handle_update()` currently does NOT call `_is_allowed` before `handle_document()`.
  In `handle_update()` (line ~158), add the check BEFORE routing to document handler:
  ```python
  if not self._is_allowed(user_id=user_id, chat_id=chat_id):
      logger.warning("telegram_bot_access_denied", chat_id=chat_id, user_id=user_id)
      return  # silently deny at update level, no reply to prevent enumeration
  ```
  Place this check right after extracting `user_id` and before the callback/document/text routing.

### Tests
- Update `tests/test_phase25_telegram_bot.py` (or whichever test file tests the bot):
  - Test: empty allowed_user_ids + open_access=False → denied
  - Test: empty allowed_user_ids + open_access=True → allowed (dev mode)
  - Test: user_id in allowed_user_ids → allowed
  - Test: user_id NOT in allowed_user_ids → denied

---

## BLOCK 2: Upload Modes — Job Examples + Negative/Positive Resume Handling

### 2a. Domain changes — `job_ftch/domain/profile.py`
Add to `SearchProfile` class (after `culture_preferences` field, before `relevance_threshold`):
```python
positive_example_texts: tuple[str, ...] = ()
negative_example_texts: tuple[str, ...] = ()
```
These are plain strings — valid in domain/ (no external imports).
Also update the `normalize` `@model_validator` to strip these tuples:
```python
for field_name in (
    ...,  # existing fields
    "positive_example_texts",
    "negative_example_texts",
):
    values = getattr(self, field_name)
    normalized = tuple(v.strip() for v in values if v.strip())
    object.__setattr__(self, field_name, normalized)
```

### 2b. New helper function — `job_ftch/adapters/profile_inputs.py`
Add function `add_example_to_profile`:
```python
def add_example_to_profile(
    managed: ManagedCandidateProfile,
    text: str,
    *,
    kind: str,  # "positive_resume", "negative_resume", "positive_job", "negative_job"
) -> ManagedCandidateProfile:
    """Add a text example to the first search profile of the candidate."""
    from datetime import UTC, datetime
    if not managed.profile.search_profiles:
        return managed
    sp = managed.profile.search_profiles[0]
    text_trimmed = text.strip()[:5000]
    if kind.startswith("negative"):
        updated_sp = sp.model_copy(
            update={"negative_example_texts": sp.negative_example_texts + (text_trimmed,)}
        )
    else:
        updated_sp = sp.model_copy(
            update={"positive_example_texts": sp.positive_example_texts + (text_trimmed,)}
        )
    updated_profiles = (updated_sp,) + managed.profile.search_profiles[1:]
    updated_profile = managed.profile.model_copy(
        update={"search_profiles": updated_profiles}
    )
    return ManagedCandidateProfile(
        user_id=managed.user_id,
        profile_id=managed.profile_id,
        profile=updated_profile,
        updated_at=datetime.now(UTC),
    )
```

### 2c. Upload mode state + new commands — `job_ftch/adapters/telegram_bot/bot.py`

Add to `TelegramBotService.__init__`:
```python
self._upload_mode: dict[int, str] = {}  # user_id -> mode
```

Add new command `/mode` in `handle_command()`:
```
/mode <positive_resume|negative_resume|positive_job|negative_job>
```
Sets `self._upload_mode[user_id] = args[0]` and replies with confirmation.
Default mode (when not set) is `positive_resume`.

Modify `handle_document()`:
- After extracting text, check `mode = self._upload_mode.get(user_id, "positive_resume")`
- If mode == "positive_resume": use existing `build_profile_from_resume_text()` (current behavior)
- If mode == "negative_resume": use `build_profile_from_resume_text()` to build profile, then:
  - Load existing active profile for this user if any
  - Call `add_example_to_profile(existing_profile, text, kind="negative_resume")`
  - Save updated profile
  - If no existing profile: create new one but mark example as negative
- If mode == "positive_job" or "negative_job":
  - Load active profile for this user (must exist — send error if not)
  - Call `add_example_to_profile(active_profile, text, kind=mode)`
  - Save updated profile
  - Reply: "Job example added as {mode.replace('_', ' ')} to your profile."

Add imports at top of bot.py:
```python
from job_ftch.adapters.profile_inputs import (
    add_example_to_profile,  # add this to existing import
    build_candidate_profile_from_payload,
    build_profile_from_resume_text,
)
```

### Tests
- Update `tests/test_profile_from_resume.py`: test `add_example_to_profile` with positive/negative kinds
- Add test for `/mode` command routing

---

## BLOCK 3: Source Validation on /addsources

### New file: `job_ftch/adapters/source_validator.py`
```python
"""Reachability checks for URL and Telegram sources before adding to tenant."""
from __future__ import annotations

import httpx
import structlog

logger = structlog.get_logger(__name__)


async def check_url_reachable(url: str, *, timeout: float = 10.0) -> tuple[bool, str]:
    """HEAD then GET check on a URL. Returns (ok, reason)."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            try:
                resp = await client.head(url)
            except Exception:
                resp = await client.get(url)
            if resp.status_code < 400:
                return True, ""
            return False, f"HTTP {resp.status_code}"
    except Exception as exc:
        return False, str(exc)


async def validate_sources(
    links: list[str],
    *,
    telegram_client: object | None = None,
) -> dict[str, tuple[bool, str]]:
    """Validate a list of source links. Returns {link: (ok, reason)}."""
    import asyncio
    results: dict[str, tuple[bool, str]] = {}
    for link in links:
        is_tg = link.startswith("@") or "t.me/" in link or link.startswith("https://t.me")
        if is_tg and telegram_client is not None:
            try:
                entity = link.lstrip("@").replace("https://t.me/", "").replace("t.me/", "")
                # Telethon: try get_entity — if it raises, channel not accessible
                from telethon import TelegramClient as _TelegramClient  # type: ignore[import]
                if isinstance(telegram_client, _TelegramClient):
                    await telegram_client.get_entity(entity)
                results[link] = (True, "")
            except Exception as exc:
                results[link] = (False, str(exc))
        elif is_tg:
            # No Telegram client available — assume reachable (can't verify)
            results[link] = (True, "no_telegram_client")
        else:
            ok, reason = await check_url_reachable(link)
            results[link] = (ok, reason)
    return results
```

### Modify `job_ftch/adapters/telegram_bot/bot.py` — `/addsources` command
In the `/addsources` handler (line ~447), BEFORE the loop that adds sources:
1. Import at top of file: `from job_ftch.adapters.source_validator import validate_sources`
2. Call `validation = await validate_sources(links)` 
3. Separate valid_links = [l for l in links if validation[l][0]]
4. Build failed_validation = [(l, validation[l][1]) for l in links if not validation[l][0]]
5. If failed_validation: immediately reply with list of unreachable links + reasons
   - "The following sources are unreachable:\n" + "\n".join(f"  {l}: {r}" for l, r in failed_validation)
   - "Please fix them and resend."
6. Continue adding only `valid_links` (the rest of the existing loop logic)

### Tests
- New file `tests/test_source_validator.py`:
  - Mock httpx to return 200 → (True, "")
  - Mock httpx to return 404 → (False, "HTTP 404")
  - Mock httpx to raise exception → (False, "...")
  - TG link without telegram_client → (True, "no_telegram_client")

---

## BLOCK 4: 4-Hour Auto-Scheduler

### Context
- `job_ftch/application/scheduler.py` has `Scheduler` class with `run_forever()`.
- `job_ftch/cli.py:309` has `_run_scheduler()` that uses it.
- `job_ftch/cli.py:424` runs the bot with `asyncio.run(run_polling_loop(service=service, client=client))`.
- The Scheduler runs `settings.schedule_interval_seconds` interval.

### Approach
Modify `job_ftch/cli.py` `_run_telegram_bot()` function (line ~391):

1. Import `asyncio` at top (already imported).
2. Import `Scheduler` (already imported at top of cli.py: `from job_ftch.application.scheduler import Scheduler`).
3. Create a coroutine for running all tenants on schedule:
```python
async def _run_bot_with_scheduler(
    service: TelegramBotService,
    client: HttpTelegramBotClient,
    runner: TenantRunner,
    interval_seconds: int,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Run bot polling and tenant pipeline scheduler concurrently."""
    async def _scheduler_loop() -> None:
        while stop_event is None or not stop_event.is_set():
            await asyncio.sleep(interval_seconds)
            try:
                await runner.run_all()
                logger.info("scheduled_run_complete", tenants=runner.tenant_ids())
            except Exception as exc:
                logger.error("scheduled_run_failed", error=str(exc))
    
    await asyncio.gather(
        run_polling_loop(service=service, client=client, stop_event=stop_event),
        _scheduler_loop(),
    )
```

4. In `_run_telegram_bot()`, replace:
   ```python
   asyncio.run(run_polling_loop(service=service, client=client))
   ```
   with:
   ```python
   interval = settings.schedule_interval_seconds or (4 * 3600)
   asyncio.run(_run_bot_with_scheduler(
       service=service,
       client=client,
       runner=runner,
       interval_seconds=interval,
   ))
   ```

5. Add `structlog` logger at module top of cli.py if not already: 
   `logger = structlog.get_logger(__name__)`
   (Check if it exists first — grep for `logger =` in cli.py)

### Tests
- Update `tests/test_phase25_telegram_bot.py` or add test:
  - Test that the scheduler loop calls `runner.run_all()` after the interval
  - Use `asyncio.Event` to stop after first tick

---

## Summary of files to change

| File | Change |
|---|---|
| `job_ftch/domain/profile.py` | Add `positive_example_texts`, `negative_example_texts` to SearchProfile |
| `job_ftch/adapters/profile_inputs.py` | Add `add_example_to_profile()` function |
| `job_ftch/adapters/telegram_bot/bot.py` | Access hardening, upload mode, source validation import |
| `job_ftch/adapters/source_validator.py` | NEW — URL + TG reachability checks |
| `job_ftch/cli.py` | Wire auto-scheduler into bot startup |
| `tests/test_phase25_telegram_bot.py` | Update for access control + mode command |
| `tests/test_profile_from_resume.py` | Add test for add_example_to_profile |
| `tests/test_source_validator.py` | NEW — URL validation tests |

---

## Verification after implementation
Run in order:
1. `python -m ruff check job_ftch/`
2. `python -m mypy job_ftch/`
3. `python -m pytest tests/ -x -q`

All must pass. If any test fails, fix before committing.
Commit message format: `feat(bot): secure access + job examples + source validation + auto-scheduler`
