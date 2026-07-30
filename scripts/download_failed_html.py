import asyncio
import pathlib
import sys
from urllib.parse import urlparse

import yaml

try:
    import nodriver
except ImportError:
    print("nodriver not found, trying playwright")
    nodriver = None

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None


async def fetch_with_nodriver(urls, out_dir):
    print("Using nodriver...")
    browser = await nodriver.start(browser_args=["--disable-blink-features=AutomationControlled"])
    for url in urls:
        domain = urlparse(url).netloc
        safe_name = domain.replace(".", "_") + ".html"
        out_file = out_dir / safe_name
        if out_file.exists():
            print(f"Skipping {url}, {out_file} already exists.")
            continue

        print(f"Fetching {url}...")
        try:
            page = await browser.get(url)
            await asyncio.sleep(5)  # Wait for JS to render
            html = await page.get_content()

            with open(out_file, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"Saved {out_file}")
        except Exception as e:
            print(f"Failed {url}: {e}")
    browser.stop()


async def fetch_with_playwright(urls, out_dir):
    print("Using playwright...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        for url in urls:
            domain = urlparse(url).netloc
            safe_name = domain.replace(".", "_") + ".html"
            out_file = out_dir / safe_name
            if out_file.exists():
                print(f"Skipping {url}, {out_file} already exists.")
                continue

            print(f"Fetching {url}...")
            try:
                await page.goto(url, wait_until="networkidle", timeout=15000)
                await asyncio.sleep(3)
                html = await page.content()

                with open(out_file, "w", encoding="utf-8") as f:
                    f.write(html)
                print(f"Saved {out_file}")
            except Exception as e:
                print(f"Failed {url}: {e}")
        await browser.close()


async def main():
    yaml_file = pathlib.Path(
        "fixtures/real_world/career_site_ingest_working_urls_20260704_183056_837e5076.yaml"
    )
    if not yaml_file.exists():
        print(f"Error: {yaml_file} not found")
        sys.exit(1)

    with open(yaml_file, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # Include both parsed_failed and not_run_in_ingest_benchmark
    failed_urls = [
        item["url"] for item in data.get("sites", []) if item.get("parse_status") != "parsed_ok"
    ]
    print(f"Found {len(failed_urls)} problematic urls.")

    out_dir = pathlib.Path("fixtures/real_world/failed_parsers_html")
    out_dir.mkdir(parents=True, exist_ok=True)

    if nodriver:
        await fetch_with_nodriver(failed_urls, out_dir)
    elif async_playwright:
        await fetch_with_playwright(failed_urls, out_dir)
    else:
        print("No browser automation library found.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
