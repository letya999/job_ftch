# Fix Plan: 5 E2E Probe Issues — 2026-06-13

## Bug 1: RSS source factory registered as "rss" but spec type is "rss_feed"
File: job_ftch/infrastructure/sources/realtime/rss.py
Fix: Change `@register_source_spec("rss")` to `@register_source_spec("rss_feed")`
Also check if there are any existing references to "rss" type spec (in tests or YAML configs) and
update them to "rss_feed". Run `grep -r '"rss"' job_ftch/ tests/ config/` first to check for
dependent code that uses the old "rss" string.
If existing tests use @register_source_spec("rss") or type="rss" in fixtures, update them too.

## Bug 2: Telegram session path mismatch
File: config/sources_e2e_20260613.yaml
The YAML has `auth_source_id: telegram` for all 40 Telegram sources. This routes
`_build_client_v2` to look for session at `.runtime/telegram/telegram.session`.
But the actual authenticated session is at `.runtime/telegram-dev.session`
(from JOB_FTCH_TELEGRAM_SESSION_PATH in .env.dev).

Fix: Remove `auth_source_id: telegram` from ALL Telegram source entries in the YAML.
When auth_source_id is None/absent, `_build_client_v2` falls back to `settings.telegram_session_path`
which correctly reads `.runtime/telegram-dev.session`.

## Bug 3: SPA sites need networkidle instead of domcontentloaded
File: config/sources_e2e_20260613.yaml
The yandex_jobs, ozon_tech, x5_tech_career entries currently have:
  monitor_config:
    render: true
    wait: domcontentloaded
`domcontentloaded` fires before XHR data loads. Change `wait` to `networkidle` for all 3.

## Bug 4: HH.ru/HH.kz BOT_BLOCKED — no User-Agent header
File: config/sources_e2e_20260613.yaml
`OfficialAPISource` uses a plain httpx client (no adaptive bypass). HH API returns 403 without
proper User-Agent. Fix: add headers to hh_ru and hh_kz specs:
  headers:
    User-Agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    Accept: "application/json"

## Bug 5: Probe asyncio timeout kills escalation before httpx can fire
File: scripts/e2e_probe.py
The `asyncio.wait_for(TIMEOUT_S=30)` fires and raises CancelledError which bypasses
the career_site_source's `_try_escalate_bypass` (which only catches httpx exceptions).
This means timeout sources never get to try stealth_browser tier in the probe.

Fix: Do NOT use asyncio.wait_for for career_site type sources. Instead, let the pipeline's
own internal httpx timeout handle it. For career_site sources, just collect items without
an outer timeout (or use a much larger outer timeout like 120s).

In the probe's `probe_source` function, check the source type from the spec_dict, and
use `TIMEOUT_S = 30` only for rest_api, rss_feed, telegram types; use `120` for career_site.

## After all fixes: re-run probe
Run: `python scripts/e2e_probe.py 2>scripts/e2e_probe_stderr.txt`
Capture full output table and summary. Report how many moved from previous status to OK.
