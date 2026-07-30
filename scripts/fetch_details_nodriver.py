import asyncio
from pathlib import Path
from urllib.parse import urljoin

import nodriver as uc
from selectolax.parser import HTMLParser

from job_ftch.infrastructure.sources.url_scoring import score_job_url

sites = [
    "https://ijob.am/",
    "https://jobfinder.am/",
    "https://www.cypruswork.com/",
    "https://job.uz/",
    "https://job.kaspi.kz/",
]


async def process_site(url: str, browser: uc.Browser):
    print(f"\nProcessing {url}")
    page = await browser.get(url)
    await asyncio.sleep(5)  # Wait for SPA

    html = await page.get_content()
    soup = HTMLParser(html)

    links = []
    for a in soup.css("a"):
        href_attr = a.attributes.get("href")
        if not href_attr:
            continue
        href = urljoin(url, href_attr)
        score = score_job_url(href, board_url=url)
        if score > 0:
            links.append((score, href))

    if not links:
        print("No job links found!")
        return

    links.sort(reverse=True, key=lambda x: x[0])
    best_link = links[0][1]
    print(f"Selected best link: {best_link} (score: {links[0][0]})")

    # Visit detail page
    page2 = await browser.get(best_link)
    await asyncio.sleep(4)
    detail_html = await page2.get_content()

    name = url.split("//")[1].split("/")[0].replace(".", "_") + "_detail.html"
    out_path = Path(f"fixtures/real_world/failed_parsers_html/{name}")
    out_path.write_text(detail_html, encoding="utf-8")
    print(f"Saved {len(detail_html)} bytes to {name}")


async def main():
    browser = await uc.start(headless=True)
    try:
        for site in sites:
            try:
                await process_site(site, browser)
            except Exception as e:
                print(f"Failed {site}: {e}")
    finally:
        browser.stop()


if __name__ == "__main__":
    asyncio.run(main())
