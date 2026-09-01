# Scaffnew: FastAPI integration adapter. NOT part of the MVP deploy.
FROM python:3.12-slim@sha256:e5c9fa26ffb76e11e0f054f30dc2523a2f9693f0c36c0cf1e39b27e152d899fc

WORKDIR /app
COPY . /app

RUN /usr/local/bin/python -m pip install --no-cache-dir "uv==0.9.26" \
    && uv sync --locked --no-dev --extra api --extra sqlite --extra openai

ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app
EXPOSE 8080

# create_app(builder) returns an ASGI app. Wire your PipelineBuilder in a small
# composition module and serve it with uvicorn (see README.md). This default
# command only prints guidance so the image build stays meaningful as a scaffnew.
CMD ["python", "-c", "print('job_ftch FastAPI adapter scaffnew — see adapters/fastapi/README.md to wire a PipelineBuilder and serve create_app() with uvicorn.')"]
