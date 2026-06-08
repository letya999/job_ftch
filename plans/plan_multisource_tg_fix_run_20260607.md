# Plan: Fix TG v2 rate-limiting + multi-source 11-channel run

## Branch
`feat/phase-12` (current, clean)

## Context

User wants to run multi-source pipeline against 11 Telegram channels
(each: last 50 posts + last 10 posts × 50 comments = ~130 API calls total).

Two bugs must be fixed FIRST:
1. `_build_client_v2` in `infrastructure/sources/telegram.py` does NOT apply
   `flood_sleep_threshold`, `timeout`, `request_retries`, `connection_retries`,
   `retry_delay` from Settings to the created TelegramClient.
2. V2 source factories (`_build_telegram_channel_source_v2`,
   `_build_telegram_group_source_v2`, `_build_telegram_comments_source_v2`)
   do NOT pass `wait_time` to the source constructors. This means when using
   `--sources-file`, the `JOB_FTCH_TELEGRAM_HISTORY_WAIT_TIME_SECONDS` setting
   is completely ignored — sources run at full speed and can trigger flood bans.

Environment:
- Session: `.runtime/telegram-dev.session`
- Settings loaded from `.env.dev`
- `JOB_FTCH_TELEGRAM_API_ID` is set in `.env.dev`
- No `JOB_FTCH_TELEGRAM_HISTORY_WAIT_TIME_SECONDS` set → defaults to 1.0s

---

## Part 1: Bug fixes in `infrastructure/sources/telegram.py`

### Fix A: `_build_client_v2` — apply all Settings to the client

Current code (broken):
```python
def _build_client_v2(auth_id: str | None, auth: AuthProvider) -> Any:
    from config import get_settings
    settings = get_settings()
    creds = auth.resolve(auth_id or "telegram")
    api_id_val = creds.get("api_id") or (str(settings.telegram_api_id) if settings.telegram_api_id else None)
    api_hash = creds.get("api_hash") or settings.telegram_api_hash
    ...
    return TelegramClient(str(session_path), int(api_id_val), api_hash)
    # BUG: no timeout, no retries, no flood_sleep_threshold applied
```

Fixed version: after creating the client, apply:
```python
client = TelegramClient(
    str(session_path),
    int(api_id_val),
    api_hash,
    timeout=settings.telegram_timeout_seconds,
    request_retries=settings.telegram_request_retries,
    connection_retries=settings.telegram_connection_retries,
    retry_delay=settings.telegram_retry_delay_seconds,
)
client.flood_sleep_threshold = settings.telegram_flood_sleep_threshold_seconds
return client
```

### Fix B: V2 factories — pass `wait_time`

All three v2 factory functions need to read `wait_time` from settings and pass it.

`_build_telegram_channel_source_v2`:
```python
@register_source_v2("telegram_channel")
def _build_telegram_channel_source_v2(spec: TelegramChannelSpec, auth: AuthProvider) -> TelegramChannelSource:
    from config import get_settings
    settings = get_settings()
    return TelegramChannelSource(
        _build_client_v2(spec.auth_source_id, auth),
        spec.entity,
        limit=spec.limit,
        wait_time=settings.telegram_history_wait_time_seconds,
        own_client=True,
    )
```

`_build_telegram_group_source_v2` — same pattern, add `wait_time`.

`_build_telegram_comments_source_v2` — same pattern, add `wait_time`.

---

## Part 2: Create `config/tg_11ch_safe.yaml`

Create a YAML config with all 11 channels specified by the user:
neuraldeep, ai_grably, kdoronin, max_about_ai, ai_driven,
the_ai_architect, deksden_notes, drugoi_dev, meetdeadlines, nobilix, countwithsasha.

For each channel:
- `type: telegram_channel`, `limit: 50`, `source_name: {name}`
- `type: telegram_comments`, `post_limit: 10`, `comment_limit_per_post: 50`, `source_name: {name}_comments`

Total sources: 22 entries (11 channels + 11 comment sources).

Rate-limit estimate:
- Channel posts: 11 × 1 batch = 11 API calls
- Comment post lists: 11 × 1 batch = 11 API calls
- Comment threads: 11 × 10 posts = 110 API calls
- Total: ~132 calls × 1.0s wait = ~2.2 minutes — very safe.

---

## Part 3: Run the pipeline

After applying fixes, run:
```
uv run python app.py \
  --sources-file config/tg_11ch_safe.yaml \
  --output-path artifacts/tg_11ch/jobs.json \
  --jsonl \
  --max-items 500
```

Set env overrides for safe operation:
- `JOB_FTCH_TELEGRAM_HISTORY_WAIT_TIME_SECONDS=2.0` — 2s between batches
- `JOB_FTCH_TELEGRAM_FLOOD_SLEEP_THRESHOLD_SECONDS=120` — auto-sleep up to 2 min
- `JOB_FTCH_LLM_BACKEND=heuristic` — no OpenAI calls during testing
- `JOB_FTCH_PIPELINE_MAX_ITEMS_PER_RUN=500`

Command with env overrides:
```
JOB_FTCH_TELEGRAM_HISTORY_WAIT_TIME_SECONDS=2.0 \
JOB_FTCH_TELEGRAM_FLOOD_SLEEP_THRESHOLD_SECONDS=120 \
JOB_FTCH_LLM_BACKEND=heuristic \
uv run python app.py \
  --sources-file config/tg_11ch_safe.yaml \
  --output-path artifacts/tg_11ch/jobs.json \
  --jsonl \
  --max-items 500
```

---

## Part 4: Verification after run

Check the results:
1. `wc -l artifacts/tg_11ch/jobs.json` — count output lines
2. Check `artifacts/debug/rejected.jsonl` for drop reasons
3. Grep output for source_name diversity (verify all 11 channels contributed)
4. Check `run.log` for any flood-wait events

---

## Files to modify

- `infrastructure/sources/telegram.py` — fixes A and B (DO NOT change anything else)

## Files to create

- `config/tg_11ch_safe.yaml` — 11 channels × (channel + comments) = 22 sources
- `artifacts/tg_11ch/` — directory for output (created automatically by pipeline)

---

## Constraints

- Do NOT change SourceSpec models (no new fields needed — wait_time is a runtime setting)
- Do NOT change `application/` contracts
- All existing tests must still pass after the fix
- Run `uv run ruff check . && uv run mypy .` after the fix
- The pipeline run itself is just observation — report what comes back
- If Telegram returns FloodWait during run: log it and let flood_sleep_threshold handle it

---

## Expected outcome

After fix + run:
- ~550 raw items fetched (50 posts × 11 channels + up to 5500 comments but many empty)
- Items flow through triage → dedup → heuristic extraction → relevance filter
- Report: fetched / triaged / extracted / emitted counts by source_kind
- Verify CompositeSource correctly fans in from all 22 source entries sequentially
