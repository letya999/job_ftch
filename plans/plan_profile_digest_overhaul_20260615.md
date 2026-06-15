# Plan: Profile examples + digest/run overhaul

Date: 2026-06-15

## Core concept (clarified by user)
- Each uploaded PDF → parsed with LLM → stored as a separate object
- No "active profile" / "activation" concept AT ALL
- All positive PDFs for a user → positive_example_texts in search profile
- All negative PDFs for a user → negative_example_texts in search profile
- These examples are used in hybrid search scoring/reranking
- One unified search profile per user (profile_id = `user_{user_id}`) that accumulates all their examples

---

## Bugs to fix

### BUG 1 CRITICAL: Missing `import time` in search_digest.py
File: `adapters/telegram_bot/handlers/search_digest.py`
Line 58 uses `time.time()` but `import time` is missing at the top.
This causes NameError on every /digest call — add `import time` to imports.

### BUG 2: runner.get_job() in digest pagination
File: `adapters/telegram_bot/handlers/search_digest.py` line 102
Verify `runner.get_job(jid, tenant_id=tenant_id)` matches the actual method signature.
If `get_job` doesn't accept `tenant_id` kwarg, fix accordingly.

### BUG 3: LLM extraction silent fallback
File: `job_ftch/application/profile_inputs.py` in `_extract_resume_payload()`
Add structured log lines:
- If LLM path used: `logger.info("resume_extraction_llm")`
- If fallback heuristic: `logger.warning("resume_extraction_heuristic_fallback", has_provider=llm_provider is not None)`
Import structlog at top if not present.

---

## Changes required

### 1. `adapters/telegram_bot/handlers/upload.py` — Remove activation, single accumulating profile

New logic:
- profile_id for a user is always `user_{user_id}` (single profile per user)
- On positive resume upload:
  1. Download + parse PDF → text
  2. LLM extract → `build_profile_from_resume_text_async(text, llm_provider=llm_provider)`
  3. Load existing profile if exists: `runner.get_candidate_profile(tenant_id, user_id_str, f"user_{user_id_str}")`
  4. If exists: `merge_resume_profile(existing, extracted, is_negative=False)` to ADD to positive_example_texts
  5. If not exists: use extracted directly
  6. Re-embed: `embed_profile_examples(managed, embedding_provider)`
  7. Save with profile_id=`user_{user_id_str}`: `runner.save_candidate_profile(tenant_id, managed)`
  8. NO call to set_active_candidate_profile anywhere
  9. Reply: "Positive resume added. Profile now has {len(positive_example_texts)} positive, {len(negative_example_texts)} negative examples.\nDetected roles: {top 3}\nDetected skills: {top 5}"

- On negative resume upload:
  1. Download + parse PDF → text
  2. LLM extract → `build_profile_from_resume_text_async(text, llm_provider=llm_provider)`
  3. Load existing profile: `runner.get_candidate_profile(tenant_id, user_id_str, f"user_{user_id_str}")`
  4. If no existing profile: create empty base profile first with `build_profile_from_resume_text_async` on empty/placeholder text, then merge negative into it
  5. Merge negative: `merge_resume_profile(existing, extracted, is_negative=True)` to ADD to negative_example_texts
  6. Re-embed
  7. Save
  8. NO set_active_candidate_profile calls
  9. Reply: "Negative resume added. Profile now has {pos_count} positive, {neg_count} negative examples."

- Simplify /mode keyboard: keep only "Positive Resume" and "Negative Resume" buttons (remove Positive Job / Negative Job from the keyboard UI for now — too confusing for users)

### 2. `adapters/telegram_bot/handlers/profiles.py` — Remove activation commands

REMOVE these handlers entirely (delete the functions and router decorators):
- `cmd_activateprofile` (Command("activateprofile"))
- `cmd_deactivateprofile` (Command("deactivateprofile"))
- `cmd_saveprofile` (Command("saveprofile"))

KEEP but simplify `cmd_profiles`:
- Load profile `user_{user_id_str}` from first tenant
- If not found: "No profile yet. Upload a resume with /mode then send a PDF."
- If found, show:
  ```
  Your search profile:
  Positive examples: N
  Negative examples: M
  Roles: role1, role2, role3
  Skills: skill1, skill2, skill3, skill4, skill5
  Updated: {updated_at date}
  
  Use /list_examples to see all examples.
  ```

KEEP `cmd_list_examples` and `cmd_delete_example` as-is (they're useful for debugging).
Update `cmd_list_examples` to look up profile_id as `user_{user_id_str}` instead of via active_profile_id.
Update `cmd_delete_example` similarly — remove the `get_active_candidate_profile_id` call, use `user_{user_id_str}` directly.

### 3. `adapters/telegram_bot/formatter.py` — Rich card format

Replace `format_job_digest` with a card format (one job):
```
<b>{title}</b> — {company}
{location} • {work_mode}

{description snippet up to 300 chars}

{url_label}: {canonical_url}
```

Where url_label = "Telegram" if "t.me" in url else "Source".
Keep the total under 3800 chars.

Also update `format_job_message` (used in /search) to same format.

The inline keyboard with "Open URL" button already exists in search_digest.py — keep it, it should work alongside the text.

### 4. `adapters/telegram_bot/handlers/search_digest.py` — Post-fix digest pagination

After fixing import time, verify:
- /digest shows one card per page (it already does this since last session's fix)
- DigestPage callback correctly loads job by job_id from FSM state
- `runner.latest_jobs(tenant_id, limit=pool_size, user_id=user_id_str)` — the user_id here allows the runner to rerank using the user's profile if it exists

Also: in `cmd_digest`, pass `user_id=user_id_str` to `runner.latest_jobs` so the profile-based reranking kicks in automatically when the user has a profile.

### 5. `adapters/telegram_bot/handlers/admin.py` — Send matched jobs after /run

After pipeline completes with emitted > 0:
- Call `runner.latest_jobs(tenant_id, limit=config.digest_size, user_id=user_id_str)` where user_id_str is the admin who triggered /run
- For each job in results, send a rich card message (using format_job_digest(jobs, page=i, page_size=1))
- Add inline "Open URL" button for each

If emitted == 0:
- Send: "No new jobs this run. {dropped} already processed. Use /digest to browse all."

### 6. `adapters/telegram_bot/handlers/base.py` — Update /help

Remove from command list:
- /saveprofile
- /activateprofile

Update /profiles description to: "Your search profile and example counts"

### 7. `adapters/telegram_bot/main.py` — Update setMyCommands

Remove `activateprofile`, `saveprofile`, `deactivateprofile` from BotCommand list.

---

## How profile is used in search (no change needed, just verify)

`runner.search_jobs(query, user_id=user_id_str)` and `runner.latest_jobs(tenant_id, user_id=user_id_str)` already look up the candidate profile by user_id. The profile object has `positive_example_texts` and `negative_example_texts` accumulated from all uploaded PDFs and embedding vectors computed. These are used for scoring in hybrid search. No pipeline changes needed — just making sure the profile is correctly built and saved.

---

## After implementation: rebuild and test
```
docker compose up -d --build
```
Then test:
1. /digest → should show rich card with URL, no crash
2. /mode → Positive Resume button, send PDF → should show "1 positive example. Roles: ..."
3. /mode → Negative Resume → send PDF → "1 negative example added"
4. /profiles → shows accumulated profile stats
5. /run ai_jobs → after completion, sends top matched job cards

## Flow
Use defaultFlow from flow.config.json (Gemini primary).
