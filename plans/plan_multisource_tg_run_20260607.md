# Plan: Fix multi-source Telegram path + create 11-channel config + run pipeline

## Context

User wants to run the Phase 11 CompositeSource pipeline on 11 Telegram channels,
fetching both channel posts AND last ~50 comments per channel, without getting banned.

Two critical bugs were identified that block this from working at all:

---

## Bug 1: `_NullAuthProvider` used in `build_composite_source_from_file`

**File**: `app.py`, function `build_composite_source_from_file`

**Current code (wrong)**:
```python
child_sources = [create_source_from_spec(spec) for spec in specs]
```

`create_source_from_spec(spec)` with no `auth` arg defaults to `_NullAuthProvider`.
`_NullAuthProvider.resolve()` returns `{}`.
`_build_client_v2` then raises `ValueError: Telegram credentials missing`.

**Fix**: instantiate `EnvAuthProvider` and pass it:
```python
from infrastructure.auth.env_auth import EnvAuthProvider

auth = EnvAuthProvider()
child_sources = [create_source_from_spec(spec, auth) for spec in specs]
```

---

## Bug 2: Session file mismatch in `_build_client_v2`

**File**: `infrastructure/sources/telegram.py`, function `_build_client_v2`

**Current code (wrong)**:
```python
session_path = Path(".runtime/telegram") / f"{auth_id or 'default'}.session"
```

Creates a NEW unauthenticated session at `.runtime/telegram/default.session`.
But the existing authenticated session is at `.runtime/telegram-dev.session`
(set via `JOB_FTCH_TELEGRAM_SESSION_PATH` in `.env.dev`).

**Fix**: when `auth_source_id` is None, read `JOB_FTCH_TELEGRAM_SESSION_PATH` from env
and fall back to `JOB_FTCH_TELEGRAM_API_ID`/`JOB_FTCH_TELEGRAM_API_HASH` if
`EnvAuthProvider` returns no creds (bridging v1 settings and v2 auth worlds):

```python
def _build_client_v2(auth_id: str | None, auth: AuthProvider) -> Any:
    import os
    from pathlib import Path
    from telethon import TelegramClient

    creds = auth.resolve(auth_id or "telegram")

    # Fallback to Settings-style env vars when AuthProvider returns nothing
    api_id_val = creds.get("api_id") or os.getenv("JOB_FTCH_TELEGRAM_API_ID")
    api_hash = creds.get("api_hash") or os.getenv("JOB_FTCH_TELEGRAM_API_HASH")

    if not api_id_val or not api_hash:
        msg = f"Telegram credentials (api_id, api_hash) missing for auth_source_id: {auth_id!r}"
        raise ValueError(msg)

    if not auth_id:
        # Reuse the configured session (already authenticated)
        raw = os.getenv("JOB_FTCH_TELEGRAM_SESSION_PATH", ".runtime/telegram/default.session")
        session_path = Path(raw.removesuffix(".session"))
    else:
        session_path = Path(".runtime/telegram") / auth_id

    session_path.parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(str(session_path), int(api_id_val), api_hash)
```

---

## New file: `config/tg_channels_11.yaml`

Create with 11 channels (5 posts each) + 11 comment sources (3 posts × 20 comments).
Use `source_name` to give each source a readable label.

Sequential execution (CompositeSource default concurrency=1) prevents flooding.
Telethon handles FloodWait with automatic backoff.

Channels to include:
- neuraldeep
- ai_grably
- kdoronin
- max_about_ai
- ai_driven
- the_ai_architect
- deksden_notes
- drugoi_dev
- meetdeadlines
- nobilix
- countwithsasha

Config structure (22 entries total: 11 channel + 11 comments):
```yaml
sources:
  # --- channels ---
  - type: telegram_channel
    entity: neuraldeep
    limit: 5
    source_name: neuraldeep_channel

  - type: telegram_channel
    entity: ai_grably
    limit: 5
    source_name: ai_grably_channel

  # ... (repeat for all 11 channels)

  # --- comments ---
  - type: telegram_comments
    entity: neuraldeep
    post_limit: 3
    comment_limit_per_post: 20
    source_name: neuraldeep_comments

  # ... (repeat for all 11 channels)
```

---

## Run command (after fixes applied)

```bash
uv run python app.py \
  --sources-file config/tg_channels_11.yaml \
  --output-path artifacts/run_2606/multisource_tg.jsonl \
  --review-output-path artifacts/run_2606/multisource_tg_review.jsonl \
  --rejected-output-path artifacts/run_2606/multisource_tg_rejected.jsonl \
  --jsonl
```

---

## Files to MODIFY

| File | Change |
|------|--------|
| `app.py` | `build_composite_source_from_file`: import and pass `EnvAuthProvider` to `create_source_from_spec` |
| `infrastructure/sources/telegram.py` | `_build_client_v2`: fallback to settings-style env vars for creds + session path |

## Files to CREATE

| File | Description |
|------|-------------|
| `config/tg_channels_11.yaml` | 22-entry sources file (11 channels + 11 comment sources) |

## Files NOT to touch

- `domain/source_spec.py` — correct as-is
- `infrastructure/sources/composite.py` — no delays needed (Telethon handles FloodWait)
- `application/registry.py` — correct as-is
- `infrastructure/auth/env_auth.py` — correct as-is

## Constraints

- Do NOT add rate-limiting delays to `CompositeSource` — Telethon handles FloodWait
- Do NOT change `.env.dev` — existing `JOB_FTCH_TELEGRAM_API_ID` etc. are enough
- `build_composite_source_from_file` import of `EnvAuthProvider` from infrastructure is
  allowed because `app.py` is the entry point (not in `application/` layer)
- After changes: `uv run ruff check . && uv run mypy . --ignore-missing-imports` must pass
- After changes: `uv run pytest tests/ -x -q` must pass

## Verification

After implementing, run the pipeline command above and confirm:
- `pipeline_run_summary` shows `fetched > 0` for at least 3+ sources
- No `ValueError: Telegram credentials missing` in output
- No crash due to session file not found
- Sources with no job posts gracefully produce 0 emitted items (already handled by triage)
