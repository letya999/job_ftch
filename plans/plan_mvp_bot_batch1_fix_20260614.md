# Plan: MVP Bot Batch 1 Fix — Dead Code + Source Validator + Auto-Scheduler

## Context
Batch 1 was partially implemented. These items are MISSING and must be added:
1. Dead code bug in `bot.py` `/mode` command handler
2. `source_validator.py` was never created
3. Auto-scheduler wiring in `cli.py` was never done

## Architecture constraints
- domain/ zero imports outside pydantic + stdlib
- No hardcoded if/elif dispatch in core
- Commits: feat, fix, chore only. NO Co-authored-by. NO AI attribution.
- Run `python -m pytest tests/ -x -q` — all must pass
- Run `python -m ruff check job_ftch/` — must be clean

---

## FIX 1: Remove dead code in `job_ftch/adapters/telegram_bot/bot.py`

In the `/mode` command handler, Gemini accidentally left dead code after the `return` statement.

Find this exact block (around line 570-590) in bot.py:
```python
        if command == "/mode":
            valid_modes = ["positive_resume", "negative_resume", "positive_job", "negative_job"]
            if not args or args[0] not in valid_modes:
                await self._sender.send_message(
                    chat_id,
                    f"Usage: /mode <{'|'.join(valid_modes)}>\nCurrent mode: {self._upload_mode.get(user_id, 'positive_resume')}",
                )
                return
            mode = args[0]
            self._upload_mode[user_id] = mode
            await self._sender.send_message(chat_id, f"Upload mode set to: {mode}")
            return
            tenant_id = args[0]
            await self._runner.reset_tenant(tenant_id)
            await self._sender.send_message(chat_id, f"Reset {tenant_id}")
            return
        if command == "/digest":
```

Remove the 3 dead code lines (after the second `return`, before `if command == "/digest"`):
```
            tenant_id = args[0]
            await self._runner.reset_tenant(tenant_id)
            await self._sender.send_message(chat_id, f"Reset {tenant_id}")
            return
```

The correct final block should be:
```python
        if command == "/mode":
            valid_modes = ["positive_resume", "negative_resume", "positive_job", "negative_job"]
            if not args or args[0] not in valid_modes:
                await self._sender.send_message(
                    chat_id,
                    f"Usage: /mode <{'|'.join(valid_modes)}>\nCurrent mode: {self._upload_mode.get(user_id, 'positive_resume')}",
                )
                return
            mode = args[0]
            self._upload_mode[user_id] = mode
            await self._sender.send_message(chat_id, f"Upload mode set to: {mode}")
            return
        if command == "/digest":
```

---

## FIX 2: Create `job_ftch/adapters/source_validator.py`

Create this new file (it does NOT exist yet):

```python
"""Reachability checks for URL and Telegram sources before adding to tenant."""
from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


async def check_url_reachable(url: str, *, timeout: float = 10.0) -> tuple[bool, str]:
    """HEAD then GET fallback check on a URL. Returns (ok, reason)."""
    import httpx
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            try:
                resp = await client.head(url)
                if resp.status_code < 400:
                    return True, ""
                # Try GET if HEAD returned error
                resp = await client.get(url)
            except httpx.HTTPStatusError:
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
    """Validate a list of source links. Returns {link: (ok, reason)}.
    
    For Telegram links (@username or t.me/...): tries get_entity if client provided.
    For URLs: HTTP HEAD/GET reachability check.
    """
    results: dict[str, tuple[bool, str]] = {}
    for link in links:
        is_tg = link.startswith("@") or "t.me/" in link or link.startswith("https://t.me")
        if is_tg and telegram_client is not None:
            try:
                entity = link.lstrip("@").replace("https://t.me/", "").replace("t.me/", "")
                # telethon TelegramClient.get_entity raises if channel not found/accessible
                await telegram_client.get_entity(entity)  # type: ignore[union-attr]
                results[link] = (True, "")
            except Exception as exc:
                results[link] = (False, str(exc)[:120])
        elif is_tg:
            # No Telegram client — skip validation, assume ok
            results[link] = (True, "")
        else:
            ok, reason = await check_url_reachable(link)
            results[link] = (ok, reason)
    return results
```

### Wire source_validator into `/addsources` in `bot.py`

In `bot.py`, at the top imports section, add:
```python
from job_ftch.adapters.source_validator import validate_sources
```

In the `/addsources` command handler (find `if command == "/addsources":` block), 
BEFORE the existing `for link in links:` loop, insert validation:

```python
            # Validate reachability before adding
            validation = await validate_sources(links)
            failed_validation = [(l, validation[l][1]) for l in links if not validation[l][0]]
            valid_links = [l for l in links if validation[l][0]]
            
            if failed_validation:
                fail_msg = "These sources are unreachable:\n" + "\n".join(
                    f"  {l}: {r}" for l, r in failed_validation
                )
                await self._sender.send_message(chat_id, fail_msg)
                if not valid_links:
                    await self._sender.send_message(chat_id, "No valid sources to add.")
                    return
                await self._sender.send_message(
                    chat_id, f"Continuing with {len(valid_links)} valid source(s)..."
                )
            
            links = valid_links  # only add reachable ones
```

Place this block right after `links = args[1:]` and before `added_count = 0`.

---

## FIX 3: Wire auto-scheduler into bot startup in `job_ftch/cli.py`

### Add helper coroutine in `cli.py`

In `cli.py`, find `_run_telegram_bot()` function (around line 391).

After the imports inside that function (or just before `asyncio.run(...)`), add a helper:

```python
    async def _bot_with_scheduler() -> None:
        interval = settings.schedule_interval_seconds or (4 * 3600)
        stop_event: asyncio.Event = asyncio.Event()

        async def _scheduler_loop() -> None:
            while not stop_event.is_set():
                await asyncio.sleep(interval)
                if stop_event.is_set():
                    break
                try:
                    summaries = await runner.run_all()
                    logger.info(
                        "bot_scheduled_run_complete",
                        tenant_count=len(summaries),
                    )
                except Exception as exc:
                    logger.error("bot_scheduled_run_failed", error=str(exc))

        await asyncio.gather(
            run_polling_loop(service=service, client=client),
            _scheduler_loop(),
        )
```

Then replace:
```python
        asyncio.run(run_polling_loop(service=service, client=client))
```
with:
```python
        asyncio.run(_bot_with_scheduler())
```

Check that `structlog` and `logger` are available in `cli.py`. Look for `logger = structlog.get_logger(...)` at module level. If missing, add `import structlog` and `logger = structlog.get_logger(__name__)` near the top of the file (after other imports).

Also check that `asyncio` is imported in `cli.py` (it already is based on grep results).

---

## Tests to add/update

### New test file `tests/test_source_validator.py`

```python
"""Tests for source URL and Telegram validation."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_url_reachable_200(respx_mock):
    """200 response → (True, '')."""
    import respx
    import httpx
    from job_ftch.adapters.source_validator import check_url_reachable
    
    respx.head("https://example.com/jobs").mock(return_value=httpx.Response(200))
    ok, reason = await check_url_reachable("https://example.com/jobs")
    assert ok is True
    assert reason == ""


@pytest.mark.asyncio
async def test_url_unreachable_404(respx_mock):
    """404 response → (False, 'HTTP 404')."""
    import respx
    import httpx
    from job_ftch.adapters.source_validator import check_url_reachable
    
    respx.head("https://example.com/dead").mock(return_value=httpx.Response(404))
    respx.get("https://example.com/dead").mock(return_value=httpx.Response(404))
    ok, reason = await check_url_reachable("https://example.com/dead")
    assert ok is False
    assert "404" in reason


@pytest.mark.asyncio
async def test_validate_sources_mixed():
    """Mixed valid/invalid links returns correct dict."""
    from job_ftch.adapters.source_validator import validate_sources
    import httpx
    import respx
    
    with respx.mock:
        respx.head("https://good.example.com").mock(return_value=httpx.Response(200))
        respx.head("https://bad.example.com").mock(return_value=httpx.Response(503))
        respx.get("https://bad.example.com").mock(return_value=httpx.Response(503))
        
        result = await validate_sources([
            "https://good.example.com",
            "https://bad.example.com",
            "@some_tg_channel",
        ])
    
    assert result["https://good.example.com"][0] is True
    assert result["https://bad.example.com"][0] is False
    assert result["@some_tg_channel"][0] is True  # no telegram_client, assume ok
```

Note: if `respx` is not available in test deps, use `unittest.mock.patch` on `httpx.AsyncClient` instead.
Check `pyproject.toml` for available test deps before writing tests. If respx not available, use simpler mocking.

---

## Commit after all fixes pass

After `python -m pytest tests/ -x -q` and `python -m ruff check job_ftch/` both pass:

```
git add job_ftch/adapters/source_validator.py job_ftch/adapters/telegram_bot/bot.py job_ftch/cli.py
git commit -m "feat(bot): source validation + auto-scheduler + fix dead code in /mode handler"
```
