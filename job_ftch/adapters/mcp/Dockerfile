# Prefer the full local profile: docker/local-mcp/Dockerfile
# (SQLite + browsers + streamable-http, no observability stack).
#
# Minimal image kept for lightweight HTTP MCP only (no browser install).
FROM python:3.12-slim

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir ".[mcp,sqlite,openai]"

ENV PYTHONPATH=/app \
    JOB_FTCH_TRACING_ENABLED=false \
    JOB_FTCH_OPENOBSERVE_ENABLED=false

EXPOSE 8000

CMD ["job_ftch", "mcp-server", \
     "--configs-dir", "/app/config", \
     "--transport", "streamable-http", \
     "--host", "0.0.0.0", \
     "--port", "8000"]
