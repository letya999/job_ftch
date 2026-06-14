# Scaffold: FastStream worker adapter. NOT part of the MVP deploy.
FROM python:3.12-slim

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir ".[faststream,sqlite,openai]"

ENV PYTHONPATH=/app

# register_faststream_handlers() attaches pipeline handlers to a FastStream app
# bound to your broker (NATS/Redis/Kafka). Provide a composition module and run
# it with `faststream run` (see README.md).
CMD ["python", "-c", "print('job_ftch FastStream adapter scaffold — see adapters/faststream/README.md to bind a broker and register handlers.')"]
