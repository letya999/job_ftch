# Configuration

## Core variables
- `JOB_FTCH_SOURCE_BACKEND`: `local_fixture`, `telegram_channel`, `telegram_group`, `telegram_comment`, `career_site`
- `JOB_FTCH_SINK_BACKEND`: main output sink, currently `json_file`
- `JOB_FTCH_STORE_BACKEND`: current default `memory`
- `JOB_FTCH_LLM_BACKEND`: `heuristic` for offline/dev, `openai` for structured extraction
- `JOB_FTCH_POSTING_BACKEND`: `none` or `telegram_posting`

## Pipeline guards
- `JOB_FTCH_PIPELINE_MAX_ITEMS_PER_RUN`: hard cap for one run
- `JOB_FTCH_PIPELINE_MAX_TEXT_LENGTH`: sanitize-time guard against pathological inputs

## Telegram source controls
- `JOB_FTCH_TELEGRAM_API_ID`
- `JOB_FTCH_TELEGRAM_API_HASH`
- `JOB_FTCH_TELEGRAM_SESSION_PATH`
- `JOB_FTCH_TELEGRAM_ENTITY`
- `JOB_FTCH_TELEGRAM_MESSAGE_LIMIT`
- `JOB_FTCH_TELEGRAM_COMMENT_POST_LIMIT`
- `JOB_FTCH_TELEGRAM_COMMENT_LIMIT_PER_POST`
- `JOB_FTCH_TELEGRAM_HISTORY_WAIT_TIME_SECONDS`
- `JOB_FTCH_TELEGRAM_TIMEOUT_SECONDS`
- `JOB_FTCH_TELEGRAM_REQUEST_RETRIES`
- `JOB_FTCH_TELEGRAM_CONNECTION_RETRIES`
- `JOB_FTCH_TELEGRAM_RETRY_DELAY_SECONDS`
- `JOB_FTCH_TELEGRAM_FLOOD_SLEEP_THRESHOLD_SECONDS`

## Career-site controls
- `JOB_FTCH_CAREER_SITE_URL`
- `JOB_FTCH_CAREER_SITE_ALLOWED_HOSTS`
- `JOB_FTCH_CAREER_SITE_TIMEOUT_SECONDS`
- `JOB_FTCH_CAREER_SITE_MAX_RETRIES`
- `JOB_FTCH_CAREER_SITE_RETRY_DELAY_SECONDS`
- `JOB_FTCH_CAREER_SITE_MAX_CONNECTIONS`
- `JOB_FTCH_CAREER_SITE_MAX_KEEPALIVE_CONNECTIONS`
- `JOB_FTCH_CAREER_SITE_DETAIL_CONCURRENCY`

## OpenAI extraction controls
- `JOB_FTCH_OPENAI_API_KEY`
- `JOB_FTCH_OPENAI_MODEL`
- `JOB_FTCH_OPENAI_BASE_URL`
- `JOB_FTCH_OPENAI_TIMEOUT_SECONDS`
- `JOB_FTCH_OPENAI_MAX_RETRIES`

## Output targets
- `JOB_FTCH_OUTPUT_PATH`
- `JOB_FTCH_OUTPUT_JSONL`
- `JOB_FTCH_OUTPUT_SCHEMA_VERSION`
- `JOB_FTCH_QUARANTINE_OUTPUT_PATH`
- `JOB_FTCH_REVIEW_OUTPUT_PATH`
- `JOB_FTCH_REJECTED_OUTPUT_PATH`
- `JOB_FTCH_REVIEW_MAX_QUALITY_SCORE`
- `JOB_FTCH_POSTING_MIN_QUALITY_SCORE`
- `JOB_FTCH_TELEGRAM_PUBLISH_ENTITY`

## Run modes
- Local fixture run:

```bash
uv run python app.py --source-path fixtures/debug/raw_items.json --max-items 10
```

- One-off Telegram run:

```bash
uv run python app.py --source-backend telegram_channel --telegram-entity ai_jobs --once
```

- One-off career-site run:

```bash
uv run python app.py --source-backend career_site --career-site-url https://job-boards.greenhouse.io/clickhouse
```
