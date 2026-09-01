# Scaffnew: FastStream worker adapter. NOT part of the MVP deploy.
FROM python:3.12-slim@sha256:e5c9fa26ffb76e11e0f054f30dc2523a2f9693f0c36c0cf1e39b27e152d899fc

WORKDIR /app
COPY . /app

RUN /usr/local/bin/python -m pip install --no-cache-dir "uv==0.9.26" \
    && uv sync --locked --no-dev --extra faststream --extra sqlite --extra openai

ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app

# register_faststream_handlers() attaches pipeline handlers to a FastStream app
# bound to your broker (NATS/Redis/Kafka). Provide a composition module and run
# it with `faststream run` (see README.md).
CMD ["python", "-c", "print('job_ftch FastStream adapter scaffnew — see adapters/faststream/README.md to bind a broker and register handlers.')"]
