# Scaffnew: Dagster integration adapter. NOT part of the MVP deploy.
FROM python:3.12-slim@sha256:e5c9fa26ffb76e11e0f054f30dc2523a2f9693f0c36c0cf1e39b27e152d899fc

WORKDIR /app
COPY . /app

RUN /usr/local/bin/python -m pip install --no-cache-dir "uv==0.9.26" \
    && uv sync --locked --no-dev --extra dagster --extra sqlite --extra openai

ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app
EXPOSE 3000

# create_definitions(builder, specs) returns a Dagster Definitions object.
# Expose `defs` from a composition module and run `dagster dev -m <module>`
# (see README.md).
CMD ["python", "-c", "print('job_ftch Dagster adapter scaffnew — see adapters/dagster/README.md to expose Definitions and run dagster dev.')"]
