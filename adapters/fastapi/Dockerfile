# Scaffold: FastAPI integration adapter. NOT part of the MVP deploy.
FROM python:3.12-slim

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir ".[api,sqlite,openai]"

ENV PYTHONPATH=/app
EXPOSE 8080

# create_app(builder) returns an ASGI app. Wire your PipelineBuilder in a small
# composition module and serve it with uvicorn (see README.md). This default
# command only prints guidance so the image build stays meaningful as a scaffold.
CMD ["python", "-c", "print('job_ftch FastAPI adapter scaffold — see adapters/fastapi/README.md to wire a PipelineBuilder and serve create_app() with uvicorn.')"]
