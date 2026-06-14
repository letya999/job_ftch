# MVP deployable: job_ftch core + Telegram bot in a single image (polling mode).
FROM python:3.12-slim

# Create a non-privileged user
RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser

WORKDIR /app
COPY --chown=appuser:appuser . /app

# Core + Telegram source + SQLite store + OpenAI extraction + RSS feeds.
RUN pip install --no-cache-dir ".[telegram,sqlite,openai,feeds]"

# Ensure runtime directory is writable
RUN mkdir -p /app/.runtime && chown -R appuser:appuser /app/.runtime

USER appuser

# The repo-root adapters/ package is not part of the job_ftch wheel,
# so expose the project root on the import path.
ENV PYTHONPATH=/app \
    JOB_FTCH_STORE_BACKEND=sqlite \
    JOB_FTCH_JOB_BACKEND=sqlite \
    JOB_FTCH_CONFIGS_DIR=/app/config/tenants

CMD ["job_ftch", "telegram-bot"]
