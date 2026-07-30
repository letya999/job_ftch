"""Download HTML of 20 failed sites using Playwright."""

import asyncio
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

URLS = [
    ("people_andersenlab_com", "https://people.andersenlab.com/vacancies"),
    ("joblab_kz", "https://joblab.kz/"),
    ("ru_jooble_org", "https://ru.jooble.org/"),
    ("wellfound_com", "https://wellfound.com/"),
    ("arc_dev", "https://arc.dev/"),
    ("otta_com", "https://otta.com/"),
    ("cord_co", "https://cord.co/"),
    ("dynamitejobs_com", "https://dynamitejobs.com/"),
    ("remote_co", "https://remote.co/remote-jobs/"),
    ("job_ozon_ru", "https://job.ozon.ru/"),
    ("magnit-tech_ru", "https://magnit-tech.ru/career/"),
    ("x5-tech_ru", "https://x5-tech.ru/career/"),
    ("2gis_ru", "https://2gis.ru/career"),
    ("jobs_dodois_com", "https://jobs.dodois.com/"),
    ("jobs_beeline_ru", "https://jobs.beeline.ru/"),
    ("rshbdigital_ru", "https://rshbdigital.ru/career/"),
    ("gazprombank_ru", "https://www.gazprombank.ru/career/"),
    ("sovcombank_ru", "https://www.sovcombank.ru/about/career/"),
    ("mvideoeldorado_ru", "https://www.mvideoeldorado.ru/career/"),
    ("arc_dev", "https://arc.dev/en-kz/remote-jobs"),
]

OUT_DIR = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "real_world"
    / "failed_parsers_html"
    / "new_batch"
)
OUT_DIR.mkdir(parents=True, exist_ok=True)


async def download_one(name: str, url: str):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            status = resp.status if resp else None
            # Wait a bit for JS to settle
            await asyncio.sleep(3)
            html = await page.content()
            # Also capture console errors
            console_msgs = []
            page.on("console", lambda msg: console_msgs.append(f"{msg.type}: {msg.text}"))

            out_file = OUT_DIR / f"{name}.html"
            out_file.write_text(html, encoding="utf-8")
            print(f"OK  {name}  status={status}  html_len={len(html)}  -> {out_file.name}")
        except Exception as e:
            print(f"ERR {name}  {type(e).__name__}: {e}")
        finally:
            await browser.close()


async def main():
    sem = asyncio.Semaphore(4)

    async def _bounded(args):
        async with sem:
            await download_one(*args)

    await asyncio.gather(*(_bounded(a) for a in URLS))


if __name__ == "__main__":
    asyncio.run(main())
