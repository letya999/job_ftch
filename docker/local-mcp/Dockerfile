# Single-container local MCP runtime for Codex CLI + CLIProxyAPI.
# SQLite only. No OpenObserve / Langfuse / Postgres / Qdrant / heavy embeddings.
FROM python:3.12-slim

WORKDIR /app

ENV HOME=/opt/job-ftch-home \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    JOB_FTCH_TRACING_ENABLED=false \
    JOB_FTCH_OPENOBSERVE_ENABLED=false \
    JOB_FTCH_STORE_BACKEND=sqlite \
    JOB_FTCH_JOB_GROUP_STORE_BACKEND=sqlite \
    JOB_FTCH_JOB_BACKEND=sqlite \
    JOB_FTCH_SEARCH_BACKEND=sqlite

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /opt/job-ftch-home /opt/playwright /data /app/config

# Dependency metadata first for better layer caching.
COPY pyproject.toml README.md ./
RUN mkdir -p job_ftch scripts && touch job_ftch/__init__.py

# Core local stack: MCP + SQLite + OpenAI-compatible LLM + scrapers + browser.
RUN pip install --no-cache-dir \
    ".[mcp,local_mcp,sqlite,openai,feeds,site_scrapers,browser,stealth,extraction,language]"

# Chromium for career-site adaptive path (no camoufox/nodriver/torch in this image).
RUN patchright install --with-deps chromium

COPY . /app
RUN pip install --no-cache-dir --no-deps . \
    && mkdir -p /data

# Default tenant configs for local MCP (override with volume mount).
COPY docker/local-mcp/config/ /app/config/

ENV PYTHONPATH=/app \
    JOB_FTCH_CONFIGS_DIR=/app/config/tenants \
    JOB_FTCH_STORE_PATH=/data/{tenant_id}/store.db

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import socket; s=socket.create_connection(('127.0.0.1',8000),2); s.close()"

CMD ["job_ftch", "mcp-server", \
     "--configs-dir", "/app/config/tenants", \
     "--transport", "streamable-http", \
     "--host", "0.0.0.0", \
     "--port", "8000"]
