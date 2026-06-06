# Source Setup

## Telegram channels and groups
1. Copy `.env.example` to `.env`.
2. Fill `JOB_FTCH_TELEGRAM_API_ID`, `JOB_FTCH_TELEGRAM_API_HASH`, and `JOB_FTCH_TELEGRAM_SESSION_PATH`.
3. Set `JOB_FTCH_TELEGRAM_ENTITY` to a public handle like `ai_jobs` or `@ai_jobs`.
4. Choose the backend:
- `telegram_channel` for channel posts
- `telegram_group` for group messages
- `telegram_comment` for channel comment threads

## Telegram comments
- Set `JOB_FTCH_TELEGRAM_COMMENT_POST_LIMIT` to cap how many posts are inspected.
- Set `JOB_FTCH_TELEGRAM_COMMENT_LIMIT_PER_POST` to cap replies per post.
- Use `JOB_FTCH_TELEGRAM_HISTORY_WAIT_TIME_SECONDS` if Telegram starts throttling history reads.

## Career sites
1. Set `JOB_FTCH_SOURCE_BACKEND=career_site`.
2. Set `JOB_FTCH_CAREER_SITE_URL` to an HTTPS board URL.
3. Add the board host to `JOB_FTCH_CAREER_SITE_ALLOWED_HOSTS`.
4. Tune `JOB_FTCH_CAREER_SITE_TIMEOUT_SECONDS` and `JOB_FTCH_CAREER_SITE_MAX_RETRIES` if the board is slow.

## Supported current patterns
- Greenhouse boards through parser auto-detection
- BCC career board with bounded detail-page concurrency
- Yandex Jobs vacancy cards at `yandex.ru/jobs/vacancies`

## Recommended first runs
- Telegram:

```bash
uv run python app.py --source-backend telegram_channel --telegram-entity ai_jobs --max-items 20
```

- Career site:

```bash
uv run python app.py --source-backend career_site --career-site-url https://job-boards.greenhouse.io/clickhouse --max-items 20
```
