# Telegram Bot Deploy

## Local development

- Use polling mode by leaving `JOB_FTCH_AUTH_TELEGRAM_BOT_WEBHOOK_URL` unset.
- Start the bridge with tenant configs:

```bash
job_ftch mcp-server --configs-dir ./config/tenants
```

## Production webhook

- Set `JOB_FTCH_AUTH_TELEGRAM_BOT_SECRET_TOKEN`
- Set `JOB_FTCH_AUTH_TELEGRAM_BOT_WEBHOOK_URL`
- Expose `POST /webhook/telegram` behind HTTPS

## Docker Compose

Use `adapters/telegram_bot/compose.yaml` and mount the tenant config directory into `/app/config`.
