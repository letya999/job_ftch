# Prefer the full local profile: docker/local-mcp/Dockerfile
# (SQLite + browsers + streamable-http, no observability stack).
#
# Minimal image kept for lightweight HTTP MCP only (no browser install).
FROM python:3.12-slim@sha256:e5c9fa26ffb76e11e0f054f30dc2523a2f9693f0c36c0cf1e39b27e152d899fc

WORKDIR /app
COPY . /app

RUN /usr/local/bin/python -m pip install --no-cache-dir "uv==0.9.26" \
    && uv sync --locked --no-dev --extra mcp --extra sqlite --extra openai

ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app \
    JOB_FTCH_TRACING_ENABLED=false \
    JOB_FTCH_OPENOBSERVE_ENABLED=false

EXPOSE 8000

CMD ["job_ftch", "mcp-server", \
     "--configs-dir", "/app/config", \
     "--transport", "streamable-http", \
     "--host", "0.0.0.0", \
     "--port", "8000"]
