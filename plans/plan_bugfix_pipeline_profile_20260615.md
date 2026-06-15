# Bug Fix Plan: Pipeline Profile Routing + Sink Backend + Dedup Reset
Date: 2026-06-15

## Context
Audit of bot logs and source code revealed 6 bugs:
1. New LLM profile `user_{user_id}` is never registered as active → pipeline prefilter and /digest both use stale old heuristic profile
2. First positive resume upload shows "Positive examples: 0" because `positive_example_texts` is never seeded on initial create
3. `/digest` and `/search` pass only `user_id` to `latest_jobs`/`search_jobs`, relying on stale active profile lookup
4. `sink_backend` always resolves to `"json_file"` despite `JOB_FTCH_SINK_BACKEND=none` in env — because `OutputSpec.backend` defaults to `"json_file"`, making the `or`-chain in `tenant_to_settings` always return the default
5. `ozon.tech` causes `ImportError: No module named 'curl_cffi'` — `stealth` extra not installed in Docker
6. No `/reset_dedup` admin command for dev testing

## Files to Modify

### 1. `adapters/telegram_bot/handlers/upload.py`
**Two fixes:**

A) After `await runner.save_candidate_profile(tenant_id, managed)` (line 160), add:
```python
await runner.set_active_candidate_profile(tenant_id, user_id_str, profile_id)
```
This updates `candidate_profile_active:{user_id}` and the active-ids set so the pipeline prefilter and digest both pick up the new profile.

B) Fix first positive upload showing 0 examples (line 154-155):
Change:
```python
else:
    managed = extracted
```
To:
```python
else:
    managed = merge_resume_profile(extracted, extracted, is_negative=is_negative)
```
This seeds `positive_example_texts` (or `negative_example_texts`) with the first resume text on initial upload, matching the same path used for subsequent uploads.

### 2. `adapters/telegram_bot/handlers/search_digest.py`
In `cmd_digest`, change the `latest_jobs` call to pass `profile_id` directly:
```python
jobs = await runner.latest_jobs(
    digest_tenant_id,
    limit=pool_size,
    user_id=user_id_str,
    profile_id=f"user_{user_id_str}",
)
```
This bypasses the `get_active_candidate_profile_ids` lookup entirely and uses the known profile ID.

In `handle_digest_page`, when fetching individual jobs via `runner.get_job(jid)`, no profile needed — that's just by job_id.

### 3. `adapters/telegram_bot/handlers/admin.py`
A) In `cmd_run`, single-tenant path, change `latest_jobs` call to pass `profile_id`:
```python
jobs = await runner.latest_jobs(
    run_tenant_id,
    limit=config.digest_size,
    user_id=user_id_str,
    profile_id=f"user_{user_id_str}",
)
```

B) Add `/reset_dedup` admin command:
```python
@router.message(Command("reset_dedup"))
async def cmd_reset_dedup(message: Message, runner: TenantRunner) -> None:
    """Handle /reset_dedup command - clears dedup records for a tenant (dev use)."""
    args = message.text.split()[1:] if message.text else []
    if not args:
        await message.answer("Usage: /reset_dedup <tenant_id>")
        return
    tenant_id = args[0]
    runtime = runner.get_runtime(tenant_id)
    connector = runtime.store._store  # TenantStore wraps a StoreConnector
    # Delete all dedup_record:* keys scoped to this tenant
    deleted = await runtime.store.clear_dedup_records()
    await message.answer(f"Cleared dedup records for {tenant_id}: {deleted} keys removed.")
```

**IMPORTANT**: Check if `TenantStore` / `TenantRuntimeStore` has a `clear_dedup_records()` method. If not, add it OR implement the reset by scanning for keys matching `dedup_record:*` pattern in the store. Look at how `DedupNode` writes dedup records — it calls `store.remember(key)` where key is like `raw:{hash}` or `url:{url}` or `fingerprint:{hash}`. The store method is likely `set` on the kv store. Clearing them requires scanning keys with pattern `dedup_record:*` or the specific prefix used.

Look at `job_ftch/nodes/dedup.py` to find the exact key prefix, then implement `clear_dedup_records()` in the store or do it inline via the connector's scan/delete methods.

### 4. `adapters/telegram_bot/handlers/base.py`
Add `/reset_dedup` to `_ADMIN_COMMANDS`:
```python
("reset_dedup", "Clear dedup records for a tenant (dev/testing)"),
```

### 5. `adapters/telegram_bot/main.py`
Add to `_build_bot_commands`:
```python
BotCommand(command="reset_dedup", description="Clear dedup records for a tenant (dev)"),
```
Place it inside the `if config.admin_user_ids:` block.

### 6. `job_ftch/domain/tenant.py`
Change `OutputSpec.backend` and `TenantConfig.sink_backend` defaults from `"json_file"` to `None`:

```python
class OutputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    backend: str | None = None   # was: str = "json_file"
    ...

class TenantConfig(BaseModel):
    ...
    sink_backend: str | None = None   # was: str = "json_file"
```

This makes the `or`-chain in `builder.py` line 307 work correctly:
`tenant.output.backend or tenant.sink_backend or base["sink_backend"]`
→ `None or None or "none"` (from env) → `"none"` ✓

**Check**: Make sure nothing else assumes `OutputSpec.backend` is always a non-None str. If any code does `sink.backend.lower()` or similar, add a None guard. The `build_output_sinks` function uses `settings.sink_backend` (from Settings), not `tenant.output.backend` directly, so the change is safe.

### 7. `adapters/telegram_bot/Dockerfile`
Change line 13 from:
```dockerfile
RUN pip install --no-cache-dir ".[bot,postgres,openai,qdrant,feeds,site_scrapers]"
```
To:
```dockerfile
RUN pip install --no-cache-dir ".[bot,postgres,openai,qdrant,feeds,site_scrapers,stealth]"
```
This adds `curl-cffi` and `playwright-stealth` which prevents the `ImportError` crash when `ozon.tech` returns 403 and the adaptive bypass escalates to `curl_stealth`.

## Implementation Notes

### Finding clear_dedup_records implementation
Run: `grep -n "dedup\|remember\|already_processed" job_ftch/nodes/dedup.py`
And: `grep -n "def remember\|def is_known" job_ftch/application/contracts.py`
The dedup node calls `store.remember(item_id)` and `store.is_known(item_id)`.
In the KV-backed store, `remember` writes a key like `dedup_record:{item_id}`.
Look at `job_ftch/infrastructure/stores/kv_store.py` or similar to find the exact prefix.
Then in `TenantRuntimeStore`, add:
```python
async def clear_dedup_records(self) -> int:
    connector = cast("StoreConnector", self._store)
    # scan for keys matching dedup_record:* under this tenant's namespace
    keys = await connector.scan_keys(self._key("dedup_record:*"))
    for key in keys:
        await connector.delete(key)
    return len(keys)
```
If `scan_keys` doesn't exist, look for `keys_matching` or `scan` method. Check the postgres store implementation.

### After implementation
Run: `docker compose up -d --build`
Then: `docker compose logs bot --tail=50`
Verify:
- No `ModuleNotFoundError: curl_cffi` 
- `pipeline_item_dropped` shows `best_profile='user_480637186'` not old resume ID
- After uploading a resume: `Positive examples: 1` (not 0)
- `/digest` returns jobs (not "No jobs available")
- `output_path` in pipeline log should be gone (NullSink has no path) or sink_backend=none

## Order of Changes
1. tenant.py (sink_backend defaults) — safe, just changes default
2. upload.py (activate profile + first example fix)
3. search_digest.py (profile_id in latest_jobs)
4. admin.py (profile_id in latest_jobs + reset_dedup command)
5. base.py (add reset_dedup to help)
6. main.py (add reset_dedup to commands)
7. Dockerfile (add stealth extra)
