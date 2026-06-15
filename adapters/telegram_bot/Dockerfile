# MVP deployable: job_ftch core + Telegram bot in a single image (polling mode).
FROM python:3.12-slim

# Create a non-privileged user
RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser

WORKDIR /app
COPY --chown=appuser:appuser . /app

# Bot adapter (aiogram + telethon + resume parsing) + library infra:
# Postgres store, OpenAI LLM/embeddings, Qdrant vector backend, RSS feeds,
# and career-site scrapers (jmespath).
RUN pip install --no-cache-dir ".[bot,postgres,openai,qdrant,feeds,site_scrapers,stealth]"

# Ensure the application tree is writable for runtime-created artifacts
RUN chown -R appuser:appuser /app && mkdir -p /app/.runtime

USER appuser

# The repo-root adapters/ package is not part of the job_ftch wheel,
# so expose the project root on the import path. Storage/LLM/vector backends
# are selected at runtime via .env.dev (JOB_FTCH_STORE_BACKEND, etc.).
ENV PYTHONPATH=/app \
    JOB_FTCH_CONFIGS_DIR=/app/adapters/telegram_bot/config/tenants

CMD ["job_ftch", "telegram-bot"]
