import asyncio
import logging

from job_ftch.application.builder import PipelineBuilder
from job_ftch.domain.ingest_models import IngestBatchRequest

logging.basicConfig(level=logging.INFO)


async def main():
    pipe = (
        PipelineBuilder()
        .add_source("job_ftch.infrastructure.sources.declarative:DynamicSourceFactory")
        .build()
    )
    urls = [
        "https://ijob.am/",
        "https://jobfinder.am/",
        "https://www.cypruswork.com/",
        "https://job.uz/",
        "https://job.kaspi.kz/",
    ]
    req = IngestBatchRequest(urls=urls, limit=5, detail_limit=2)
    res = await pipe.execute(req)

    print("\n--- RESULTS ---")
    for r in res.results:
        print(f"{r.url}: {r.status} (Items: {r.item_count}) {r.failure_bucket}")


if __name__ == "__main__":
    asyncio.run(main())
