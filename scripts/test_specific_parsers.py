import asyncio

from job_ftch.infrastructure.bypass.adaptive import AdaptiveBypassManager
from job_ftch.infrastructure.sources.career_site import build_default_http_client
from job_ftch.infrastructure.sources.career_site_source import CareerSiteSource

urls = [
    "https://job.kaspi.kz/",
    "https://www.linkedin.com/jobs/search/",
]


async def check_site(url: str):
    print(f"\n--- Testing {url} ---")

    # Manually build the components
    http_client = build_default_http_client(verify_ssl=True)

    pass

    client = AdaptiveBypassManager(
        http_client,
    )

    source = CareerSiteSource(client, url, limit=5, own_client=True)

    try:
        count = 0
        async for item in source.fetch():
            print(f"Found item: {item.title} -> {item.url}")
            count += 1
        print(f"Total found: {count}")
    except Exception as e:
        print(f"Error fetching {url}: {e}")


async def main():
    for url in urls:
        await check_site(url)


if __name__ == "__main__":
    asyncio.run(main())
