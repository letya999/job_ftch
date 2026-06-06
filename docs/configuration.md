# Configuration

`job_ftch` uses `pydantic-settings` and reads project-owned environment
variables with the `JOB_FTCH_` prefix from `.env` and `.env.dev`.

Do not commit real `.env`, Telegram sessions, API keys, tokens, cookies, or
private source payloads.

## Loading Rules

- Local safe defaults live in `.env.example`.
- Development defaults live in `.env.dev.example`.
- Production-like placeholders live in `.env.prod.example`.
- Empty optional secret fields are treated as unset.
- Backend-specific credentials are required only when that backend is selected.

## Core Runtime

| Variable | Default | Required | Secret | Notes |
| --- | --- | --- | --- | --- |
| `JOB_FTCH_SOURCE_BACKEND` | `local_fixture` | yes | no | `local_fixture`, `telegram_channel`, `telegram_group`, `telegram_comment`, or `career_site`. |
| `JOB_FTCH_SINK_BACKEND` | `json_file` | yes | no | Only `json_file` is implemented. |
| `JOB_FTCH_STORE_BACKEND` | `memory` | yes | no | `memory` or `postgres`. Use `postgres` for production persistent idempotency. |
| `JOB_FTCH_LOG_LEVEL` | `INFO` | yes | no | One of `CRITICAL`, `ERROR`, `WARNING`, `INFO`, `DEBUG`. |
| `JOB_FTCH_TELEMETRY_SERVICE_NAME` | `job_ftch` | yes | no | OpenTelemetry service name. |
| `JOB_FTCH_TELEMETRY_CONSOLE_EXPORTER` | `false` | yes | no | Enables console span export for local debugging. |
| `JOB_FTCH_PIPELINE_MAX_ITEMS_PER_RUN` | `200` | yes | no | Hard cap for items processed in one run. |
| `JOB_FTCH_PIPELINE_MAX_SOURCE_ERRORS` | `20` | yes | no | Reserved guard for source-error abort policy. |
| `JOB_FTCH_DRY_RUN` | `false` | yes | no | Dry-run flag for side-effecting adapters and future cursor advances. |
| `JOB_FTCH_MAX_TEXT_LENGTH` | `20000` | yes | no | Raw validation limit enforced by `ValidateRawNode`. |
| `JOB_FTCH_DEDUP_THRESHOLD` | `90` | yes | no | Reserved fuzzy dedup threshold, 0-100. |

## Local Fixture Source

| Variable | Default | Required | Secret | Notes |
| --- | --- | --- | --- | --- |
| `JOB_FTCH_DEBUG_SOURCE_PATH` | `fixtures/debug/raw_items.json` | for `local_fixture` | no | JSON or JSONL fixture path. |

## Telegram Sources

Telegram source backends require read-only MTProto credentials and a target
entity. Do not commit `.session` files.

| Variable | Default | Required | Secret | Notes |
| --- | --- | --- | --- | --- |
| `JOB_FTCH_TELEGRAM_API_ID` | unset | for Telegram backends | yes | Telegram API ID. Empty string is treated as unset. |
| `JOB_FTCH_TELEGRAM_API_HASH` | unset | for Telegram backends | yes | Telegram API hash. Empty string is treated as unset. |
| `JOB_FTCH_TELEGRAM_SESSION_PATH` | `.runtime/telegram.session` | for Telegram backends | yes | Session path; the file itself is local secret state. |
| `JOB_FTCH_TELEGRAM_ENTITY` | unset | for Telegram backends | no | Channel/group username, ID, or compatible Telethon entity. |
| `JOB_FTCH_TELEGRAM_ENTITIES` | unset | future multi-source mode | no | Comma-separated reserved list of Telegram entities. Current runtime uses `JOB_FTCH_TELEGRAM_ENTITY`. |
| `JOB_FTCH_TELEGRAM_MESSAGE_LIMIT` | `100` | yes | no | Message limit for channel/group fetches. |
| `JOB_FTCH_TELEGRAM_COMMENT_POST_LIMIT` | `20` | yes | no | Number of channel posts to inspect for comments. |
| `JOB_FTCH_TELEGRAM_COMMENT_LIMIT_PER_POST` | `50` | yes | no | Comment limit per inspected post. |
| `JOB_FTCH_TELEGRAM_HISTORY_WAIT_TIME_SECONDS` | `1.0` | yes | no | Wait time passed to Telethon history iteration. |
| `JOB_FTCH_TELEGRAM_FLOOD_SLEEP_THRESHOLD_SECONDS` | `60` | yes | no | Telethon flood wait threshold. |

## Career-Site Source

| Variable | Default | Required | Secret | Notes |
| --- | --- | --- | --- | --- |
| `JOB_FTCH_CAREER_SITE_URL` | unset | for `career_site` | no | HTTPS career page/list URL. |
| `JOB_FTCH_CAREER_SITE_URLS` | unset | future multi-source mode | no | Comma-separated reserved list of career-site URLs. Current runtime uses `JOB_FTCH_CAREER_SITE_URL`. |
| `JOB_FTCH_CAREER_SITE_CONFIG_PATH` | unset | future configured adapters | no | Reserved path for config-driven career-site selectors. |
| `JOB_FTCH_CAREER_SITE_ALLOWED_HOSTS` | `job-boards.greenhouse.io,www.bcc.kz,bcc.kz` | for `career_site` | no | Comma-separated allowed hosts. |
| `JOB_FTCH_HTTP_TIMEOUT_SECONDS` | `30.0` | yes | no | HTTP timeout for career-site fetches. |
| `JOB_FTCH_HTTP_MAX_RETRIES` | `2` | yes | no | Reserved retry count for transient HTTP failures. |
| `JOB_FTCH_HTTP_MAX_PAGES_PER_SOURCE` | `50` | yes | no | Reserved source guard for paginated adapters. |

## Output Paths

The current debug runtime still emits sanitized `RawItem` records through
`JOB_FTCH_OUTPUT_PATH`. The MVP output contract adds separate paths for jobs,
rejected items, review items, and run summaries.

| Variable | Default | Required | Secret | Notes |
| --- | --- | --- | --- | --- |
| `JOB_FTCH_OUTPUT_PATH` | `artifacts/debug/raw_items.json` | yes | no | Current debug output path. |
| `JOB_FTCH_OUTPUT_JSONL` | `false` | yes | no | Current debug output mode. |
| `JOB_FTCH_QUARANTINE_OUTPUT_PATH` | `artifacts/debug/quarantine.jsonl` | yes | no | Current quarantine output path. |
| `JOB_FTCH_QUARANTINE_OUTPUT_JSONL` | `true` | yes | no | Current quarantine output mode. |
| `JOB_FTCH_JOBS_OUTPUT_PATH` | `artifacts/debug/jobs.jsonl` | yes | no | Future production job output path. |
| `JOB_FTCH_JOBS_OUTPUT_JSONL` | `true` | yes | no | Future job output mode. |
| `JOB_FTCH_REJECTED_OUTPUT_PATH` | `artifacts/debug/rejected_items.jsonl` | yes | no | Future rejected-items output path. |
| `JOB_FTCH_REJECTED_OUTPUT_JSONL` | `true` | yes | no | Future rejected-items output mode. |
| `JOB_FTCH_REVIEW_OUTPUT_PATH` | `artifacts/debug/review_items.jsonl` | yes | no | Future human-review output path. |
| `JOB_FTCH_REVIEW_OUTPUT_JSONL` | `true` | yes | no | Future review output mode. |
| `JOB_FTCH_RUN_SUMMARY_OUTPUT_PATH` | `artifacts/debug/run_summary.json` | yes | no | Future run-summary report path. |

## Persistence

| Variable | Default | Required | Secret | Notes |
| --- | --- | --- | --- | --- |
| `JOB_FTCH_POSTGRES_DSN` | unset | for `postgres` store | yes | PostgreSQL connection string for processed IDs, dedup keys, cursors, summaries, jobs, and rejections. Empty string is treated as unset. |

## LLM Extraction

LLM extraction is disabled by default. Live extraction must stay behind the
`LLMProvider` port and must not log API keys or raw credentials.

| Variable | Default | Required | Secret | Notes |
| --- | --- | --- | --- | --- |
| `JOB_FTCH_LLM_BACKEND` | `disabled` | yes | no | `disabled` or `openai`. |
| `JOB_FTCH_LLM_MODEL` | unset | for `openai` | no | Model identifier supplied by the operator. |
| `JOB_FTCH_LLM_BASE_URL` | unset | no | no | Optional compatible API base URL. |
| `JOB_FTCH_LLM_API_KEY` | unset | for `openai` | yes | Provider API key. Empty string is treated as unset. |
| `JOB_FTCH_LLM_TIMEOUT_SECONDS` | `30.0` | yes | no | Timeout for one extraction call. |
| `JOB_FTCH_LLM_MAX_RETRIES` | `2` | yes | no | Bounded retry count for extraction calls. |
| `JOB_FTCH_LLM_MAX_CALLS_PER_RUN` | `0` | yes | no | Reserved operational guard; `0` means unlimited until implemented otherwise. |

## Extraction Thresholds

| Variable | Default | Required | Secret | Notes |
| --- | --- | --- | --- | --- |
| `JOB_FTCH_EXTRACTION_MAIN_QUALITY_THRESHOLD` | `0.70` | yes | no | Future threshold for main job output. |
| `JOB_FTCH_EXTRACTION_REVIEW_QUALITY_THRESHOLD` | `0.40` | yes | no | Future threshold for review output; must be less than or equal to the main threshold. |

## Lockfile Policy

This repository is an application/CLI pipeline, not a reusable library package.
Commit `uv.lock` so local development, CI, and future release checks resolve the
same dependency graph.
