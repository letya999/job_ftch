FROM python:3.12-slim@sha256:e5c9fa26ffb76e11e0f054f30dc2523a2f9693f0c36c0cf1e39b27e152d899fc
WORKDIR /app
RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN pip install --no-cache-dir uv==0.9.26 \
    && uv sync --locked --no-dev --extra llm_queue --extra tracing --no-install-project
COPY --chown=appuser:appuser job_ftch ./job_ftch
COPY --chown=appuser:appuser config ./config
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app
USER appuser
CMD ["python", "-m", "job_ftch.infrastructure.llm.dispatch_worker", "worker"]
