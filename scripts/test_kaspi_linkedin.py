import asyncio

from job_ftch.infrastructure.sources.site_parsers.kaspi import KaspiParser
from job_ftch.infrastructure.sources.site_parsers.linkedin import LinkedinParser


class DummyClient:
    async def get(self, url, **kwargs):
        import httpx

        async with httpx.AsyncClient() as client:
            return await client.get(url, **kwargs)


async def check():
    kaspi = KaspiParser()
    print("Testing KaspiParser...")
    try:
        async for item in kaspi.extract("https://job.kaspi.kz/", client=DummyClient(), limit=3):
            print(f"Found Kaspi item: {item.title} -> {item.url}")
    except Exception as e:
        print(f"Kaspi Error: {e}")

    linkedin = LinkedinParser()
    print("\nTesting LinkedinParser...")
    try:
        async for item in linkedin.extract(
            "https://www.linkedin.com/jobs/search/?keywords=Python", client=DummyClient(), limit=3
        ):
            print(f"Found LinkedIn item: {item.title} -> {item.url}")
    except Exception as e:
        print(f"LinkedIn Error: {e}")


if __name__ == "__main__":
    asyncio.run(check())
