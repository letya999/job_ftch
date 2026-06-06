# Configuration

`job_ftch` uses `pydantic-settings` with the `JOB_FTCH_` environment prefix.
Settings are loaded from `.env` and `.env.dev`; example files are safe
templates only.

Do not commit real `.env` files, Telegram sessions, PostgreSQL credentials,
API keys, tokens, cookies, or private source payloads.

## Runtime

| Variable | Default | Required | Secret | Notes |
| --- | --- | --- | --- | --- |
| `JOB_FTCH_SOURCE_BACKEND` | `local_fixture` | yes | no | Registered source backend key. |
| `JOB_FTCH_SINK_BACKEND` | `json_file` | yes | no | Registered sink backend key. |
| `JOB_FTCH_STORE_BACKEND` | `memory` | yes | no | `memory` for local/debug, `postgres` for persistent production state. |
| `JOB_FTCH_POSTGRES_DSN` | unset | for `postgres` store | yes | PostgreSQL DSN. Empty string is treated as unset. |
| `JOB_FTCH_LOG_LEVEL` | `INFO` | yes | no | One of `CRITICAL`, `ERROR`, `WARNING`, `INFO`, `DEBUG`. |
| `JOB_FTCH_TELEMETRY_SERVICE_NAME` | `job_ftch` | yes | no | OpenTelemetry service name. |
| `JOB_FTCH_TELEMETRY_CONSOLE_EXPORTER` | `false` | yes | no | Enables local console span export. |
| `JOB_FTCH_PIPELINE_MAX_ITEMS_PER_RUN` | `200` | yes | no | Per-run processing cap. |

## Local Fixture Source

| Variable | Default | Required | Secret | Notes |
| --- | --- | --- | --- | --- |
| `JOB_FTCH_DEBUG_SOURCE_PATH` | `fixtures/debug/raw_items.json` | for `local_fixture` | no | JSON or JSONL fixture path. |

## Telegram Sources

| Variable | Default | Required | Secret | Notes |
| --- | --- | --- | --- | --- |
| `JOB_FTCH_TELEGRAM_API_ID` | unset | for Telegram backends | yes | Telegram API ID. Empty string is treated as unset. |
| `JOB_FTCH_TELEGRAM_API_HASH` | unset | for Telegram backends | yes | Telegram API hash. Empty string is treated as unset. |
| `JOB_FTCH_TELEGRAM_SESSION_PATH` | `.runtime/telegram.session` | for Telegram backends | yes | Session path; the file itself is local secret state. |
| `JOB_FTCH_TELEGRAM_ENTITY` | unset | for Telegram backends | no | Channel/group username, ID, or compatible Telethon entity. |
| `JOB_FTCH_TELEGRAM_MESSAGE_LIMIT` | `100` | yes | no | Message limit for channel/group fetches. |
| `JOB_FTCH_TELEGRAM_COMMENT_POST_LIMIT` | `20` | yes | no | Number of channel posts to inspect for comments. |
| `JOB_FTCH_TELEGRAM_COMMENT_LIMIT_PER_POST` | `50` | yes | no | Comment limit per inspected post. |
| `JOB_FTCH_TELEGRAM_HISTORY_WAIT_TIME_SECONDS` | `1.0` | yes | no | Wait time passed to Telethon history iteration. |
| `JOB_FTCH_TELEGRAM_FLOOD_SLEEP_THRESHOLD_SECONDS` | `60` | yes | no | Telethon flood wait threshold. |

## Career-Site Source

| Variable | Default | Required | Secret | Notes |
| --- | --- | --- | --- | --- |
| `JOB_FTCH_CAREER_SITE_URL` | unset | for `career_site` | no | HTTPS career page/list URL. |
| `JOB_FTCH_CAREER_SITE_ALLOWED_HOSTS` | empty tuple | for `career_site` | no | Comma-separated allowed hosts. The configured URL host must be listed. |

## Output

| Variable | Default | Required | Secret | Notes |
| --- | --- | --- | --- | --- |
| `JOB_FTCH_OUTPUT_PATH` | `artifacts/debug/raw_items.json` | yes | no | Current debug output path. |
| `JOB_FTCH_OUTPUT_JSONL` | `false` | yes | no | Current debug output mode. |
| `JOB_FTCH_QUARANTINE_OUTPUT_PATH` | `artifacts/debug/quarantine.jsonl` | yes | no | Quarantine output path. |
| `JOB_FTCH_QUARANTINE_OUTPUT_JSONL` | `true` | yes | no | Quarantine output mode. |

## Lockfile Policy

This repository is an application/CLI pipeline, not a reusable library package.
Commit `uv.lock` so local development, CI, and release checks resolve the same
dependency graph.
