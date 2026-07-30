# Scaffnew: Dagster integration adapter. NOT part of the MVP deploy.
FROM python:3.12-slim

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir ".[dagster,sqlite,openai]"

ENV PYTHONPATH=/app
EXPOSE 3000

# create_definitions(builder, specs) returns a Dagster Definitions object.
# Expose `defs` from a composition module and run `dagster dev -m <module>`
# (see README.md).
CMD ["python", "-c", "print('job_ftch Dagster adapter scaffnew — see adapters/dagster/README.md to expose Definitions and run dagster dev.')"]
