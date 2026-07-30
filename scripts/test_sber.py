import asyncio

from job_ftch.infrastructure.sources.site_parsers.sber import SberParser


async def check_sber():
    parser = SberParser()
    print("Testing SberParser...")
    async for item in parser.extract("https://rabota.sber.ru/search/", client=None, limit=5):
        print(f"Found item: {item.title} -> {item.url}")


if __name__ == "__main__":
    asyncio.run(check_sber())
