# FastMCP server: exposes job_ftch tools and job:// resources over HTTP.
FROM python:3.12-slim

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir ".[mcp,sqlite,openai]"

# The repo-root adapters/ package is not part of the job_ftch wheel.
ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["job_ftch", "mcp-server", \
     "--configs-dir", "/app/config", \
     "--transport", "http", \
     "--host", "0.0.0.0", \
     "--port", "8000"]
