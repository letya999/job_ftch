import asyncio
from pathlib import Path
from urllib.parse import urljoin

import httpx
from selectolax.parser import HTMLParser

from job_ftch.infrastructure.sources.url_scoring import score_job_url

sites = [
    "https://careers.epam.com/jobs",
    "https://trudvsem.ru/vacancy/search",
    "https://www.superjob.kg/",
    "https://www.rabota.md/ro/",
    "https://uzjobs.uz/r/vakansy.html",
]


async def fetch_detail(url: str):
    # Intentionally disables TLS verification for one-off probe collection against
    # broken upstreams. Do not reuse this client in production code.
    async with httpx.AsyncClient(  # nosec B501
        follow_redirects=True, timeout=30.0, verify=False
    ) as client:
        print(f"Fetching listing: {url}")
        res = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        soup = HTMLParser(res.text)
        links = []
        for a in soup.css("a"):
            href_attr = a.attributes.get("href")
            if not href_attr:
                continue
            href = urljoin(str(res.url), href_attr)
            score = score_job_url(href, board_url=str(res.url))
            if score > 0:
                links.append((score, href))

        if not links:
            print(f"No job links found on {url}")
            return

        links.sort(reverse=True, key=lambda x: x[0])
        best_link = links[0][1]
        print(f"Best link for {url} is {best_link} (score: {links[0][0]})")

        res_detail = await client.get(best_link, headers={"User-Agent": "Mozilla/5.0"})
        name = url.split("//")[1].split("/")[0].replace(".", "_") + "_detail.html"
        Path(f"fixtures/real_world/failed_parsers_html/{name}").write_text(
            res_detail.text, encoding="utf-8"
        )
        print(f"Saved {name}")


async def main():
    for site in sites:
        try:
            await fetch_detail(site)
        except Exception as e:
            print(f"Error on {site}: {e}")


asyncio.run(main())
