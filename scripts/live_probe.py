"""Live probe: RSS + career site DNS resolution + Telegram channels."""

from __future__ import annotations

import asyncio
import io
import os
import socket
import sys
from datetime import UTC, datetime
from typing import Any

import httpx
from dotenv import load_dotenv

# Force UTF-8 output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

load_dotenv(".env.dev")

try:
    import feedparser

    _HAS_FEEDPARSER = True
except ImportError:
    _HAS_FEEDPARSER = False

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
HTTP_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.9",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
}

# ── RSS ───────────────────────────────────────────────────────────────────────

RSS_SOURCES = [
    (
        "Habr Career: machine learning",
        "https://career.habr.com/vacancies/rss?q=machine+learning&type=1",
    ),
    ("Habr Career: LLM", "https://career.habr.com/vacancies/rss?q=llm&type=1"),
    (
        "Habr Career: data scientist",
        "https://career.habr.com/vacancies/rss?q=data+scientist&type=1",
    ),
    ("Habr Career: NLP", "https://career.habr.com/vacancies/rss?q=nlp&type=1"),
    (
        "Habr Career: AI RU",
        "https://career.habr.com/vacancies/rss?q=%D0%B8%D1%81%D0%BA%D1%83%D1%81%D1%81%D1%82%D0%B2%D0%B5%D0%BD%D0%BD%D1%8B%D0%B9+%D0%B8%D0%BD%D1%82%D0%B5%D0%BB%D0%BB%D0%B5%D0%BA%D1%82&type=1",
    ),
]

# ── Career sites: multiple domain candidates per company ─────────────────────

CAREER_CANDIDATES: list[tuple[str, list[tuple[str, str, str]]]] = [
    # ── Russia ────────────────────────────────────────────────────────────────
    (
        "Yandex (RU)",
        [
            ("career.yandex.ru HTML", "https://career.yandex.ru/vacancies", "HEAD"),
            (
                "career.yandex.ru API search",
                "https://career.yandex.ru/api/vacancies/?search=ml&limit=10",
                "GET",
            ),
            (
                "career.yandex.ru API v2",
                "https://career.yandex.ru/api/v2/vacancies?query=ml&limit=10",
                "GET",
            ),
        ],
    ),
    (
        "Sberbank (RU)",
        [
            ("rabota.sber.ru", "https://rabota.sber.ru", "HEAD"),
            (
                "rabota.sber.ru search",
                "https://rabota.sber.ru/search?query=machine+learning",
                "HEAD",
            ),
            ("sber.ru/career", "https://www.sber.ru/career", "HEAD"),
            ("sberjobs.ru", "https://sberjobs.ru", "HEAD"),
        ],
    ),
    (
        "Avito (RU)",
        [
            (
                "avito.ru vacancies",
                "https://www.avito.ru/rossiya/vakansii?q=machine+learning",
                "HEAD",
            ),
            (
                "avito.ru API",
                "https://www.avito.ru/web/5/v3/items?categoryId=4&query=ML&limit=5",
                "GET",
            ),
        ],
    ),
    (
        "VK / Team (RU)",
        [
            ("jobs.vk.com", "https://jobs.vk.com", "HEAD"),
            ("jobs.vk.com vacancies", "https://jobs.vk.com/vacancies?query=ML", "HEAD"),
            ("team.vk.company", "https://team.vk.company/vacancy", "HEAD"),
        ],
    ),
    (
        "Tinkoff (RU)",
        [
            ("tinkoff.ru/career", "https://www.tinkoff.ru/career", "HEAD"),
            (
                "tinkoff career vacancies",
                "https://www.tinkoff.ru/career/vacancies/?query=ML",
                "HEAD",
            ),
        ],
    ),
    # ── Kazakhstan ────────────────────────────────────────────────────────────
    (
        "Kaspi.kz (KZ)",
        [
            ("kaspi.kz/about/career", "https://kaspi.kz/about/career", "HEAD"),
            ("job.kaspi.kz", "https://job.kaspi.kz", "HEAD"),
            ("kaspi.kz API", "https://kaspi.kz/api/careers/vacancies?limit=10", "GET"),
            ("tech.kaspi.kz", "https://tech.kaspi.kz", "HEAD"),
        ],
    ),
    (
        "Kolesa Group (KZ)",
        [
            ("kolesa.kz/vakansii", "https://kolesa.kz/vakansii", "HEAD"),
            ("group.kolesa.kz", "https://group.kolesa.kz", "HEAD"),
            ("kolesa-group.kz", "https://kolesa-group.kz", "HEAD"),
        ],
    ),
    (
        "BI Group (KZ)",
        [
            ("bi.group/ru/career", "https://bi.group/ru/career", "HEAD"),
            ("bi.group main", "https://bi.group", "HEAD"),
            ("bigroup.kz", "https://bigroup.kz/career", "HEAD"),
        ],
    ),
    (
        "Halyk Bank (KZ)",
        [
            ("halykbank.kz career", "https://halykbank.kz/about/career", "HEAD"),
            ("halyk.bank", "https://halyk.bank/about/career", "HEAD"),
            ("job.halykbank.kz", "https://job.halykbank.kz", "HEAD"),
            ("halykjobs.kz", "https://halykjobs.kz", "HEAD"),
        ],
    ),
    (
        "Air Astana (KZ)",
        [
            ("airastana.com en careers", "https://airastana.com/kaz/en-gb/Company/Careers", "HEAD"),
            (
                "airastana.com ru careers",
                "https://airastana.com/kaz/ru-ru/Kompaniya/Vakansii",
                "HEAD",
            ),
            ("recruitment.airastana.com", "https://recruitment.airastana.com", "HEAD"),
        ],
    ),
]

# ── Telegram channels ─────────────────────────────────────────────────────────

TG_CHANNELS = [
    (
        "AI/ML content",
        [
            "@ods_ai",  # OpenDataScience — main RU DS community
            "@ml_in_production",  # Production ML
            "@deeplearningschool",  # Deep Learning School (Yandex)
            "@ru_ds",  # Russian Data Science
            "@ai_machinelearning_ru",
        ],
    ),
    (
        "AI jobs RU",
        [
            "@aiwork_ru",
            "@datascience_jobs",
            "@ml_jobs",
            "@nlp_jobs",
            "@cv_jobs_russia",
        ],
    ),
    (
        "IT jobs general RU",
        [
            "@getmatch",  # real, active
            "@habr_career",
            "@tproger_jobs",
            "@python_jobs_ru",
            "@cerebro_jobs",
        ],
    ),
    (
        "IT/AI KZ",
        [
            "@it_jobs_kz",
            "@tech_jobs_kz",
            "@aikazakhstan",
            "@jobs_kz_it",
            "@enbek_jobs",
        ],
    ),
]

TG_MSGS_PER_CHANNEL = 30
TG_DELAY_BETWEEN = 2.0


# ── Helpers ───────────────────────────────────────────────────────────────────


def dns_resolves(hostname: str) -> bool:
    try:
        socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
        return True
    except OSError:
        return False


def print_section(title: str) -> None:
    print(f"\n{'=' * 64}")
    print(f"  {title}")
    print("=" * 64)


async def probe_rss(session: httpx.AsyncClient, label: str, url: str) -> dict[str, Any]:
    try:
        r = await session.get(url, timeout=20)
        r.raise_for_status()
        if not _HAS_FEEDPARSER:
            return {"label": label, "items": 0, "titles": [], "error": "feedparser missing"}
        loop = asyncio.get_running_loop()
        feed = await loop.run_in_executor(None, feedparser.parse, r.text)
        titles = [e.get("title", "?") for e in feed.entries[:5]]
        return {"label": label, "items": len(feed.entries), "titles": titles, "error": None}
    except Exception as exc:
        return {"label": label, "items": 0, "titles": [], "error": str(exc)[:100]}


async def probe_url(
    session: httpx.AsyncClient, label: str, url: str, method: str
) -> dict[str, Any]:
    hostname = url.split("/")[2]
    if not dns_resolves(hostname):
        return {"label": label, "status": "DNS_FAIL", "ct": "", "preview": ""}
    try:
        r = (
            await session.head(url, timeout=12)
            if method == "HEAD"
            else await session.get(url, timeout=12)
        )
        ct = r.headers.get("content-type", "")[:50]
        preview = ""
        if r.status_code == 200 and method == "GET":
            if "json" in ct:
                try:
                    data = r.json()
                    preview = (
                        f"JSON keys={list(data.keys())[:5]}"
                        if isinstance(data, dict)
                        else f"JSON array[{len(data)}]"
                    )
                except Exception:
                    preview = r.text[:80].replace("\n", " ")
            else:
                preview = r.text[:80].replace("\n", " ")
        return {"label": label, "status": r.status_code, "ct": ct, "preview": preview}
    except Exception as exc:
        return {"label": label, "status": "ERR", "ct": "", "preview": str(exc)[:80]}


# ── Telegram probe ────────────────────────────────────────────────────────────


async def probe_telegram() -> None:
    print_section("TELEGRAM — live probe")

    api_id_raw = os.environ.get("JOB_FTCH_TELEGRAM_API_ID", "")
    api_hash = os.environ.get("JOB_FTCH_TELEGRAM_API_HASH", "")
    session_path = os.environ.get("JOB_FTCH_TELEGRAM_SESSION_PATH", ".runtime/telegram-dev.session")

    if not api_id_raw or not api_hash:
        print("  SKIP: credentials not set")
        return
    if not os.path.exists(session_path):
        print(f"  SKIP: session file not found at {session_path}")
        return

    try:
        from telethon import TelegramClient
        from telethon.errors import ChannelPrivateError, FloodWaitError, UsernameNotOccupiedError
    except ImportError:
        print("  SKIP: telethon not installed")
        return

    session_name = session_path.replace(".session", "")
    client = TelegramClient(session_name, int(api_id_raw), api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        print("  SKIP: session not authorized")
        await client.disconnect()
        return

    me = await client.get_me()
    print(f"  Logged in as: {me.first_name} (@{me.username})\n")

    for group_label, channels in TG_CHANNELS:
        print(f"\n  [{group_label}]")
        for channel in channels:
            await asyncio.sleep(TG_DELAY_BETWEEN)
            try:
                entity = await client.get_entity(channel)
                msgs = await client.get_messages(entity, limit=TG_MSGS_PER_CHANNEL)
                texts = [m.text for m in msgs if m.text and len(m.text) > 20]
                print(f"    OK   {channel:<28}  msgs={len(msgs)}  with_text={len(texts)}")
                if texts:
                    sample = texts[0][:100].replace("\n", " ")
                    print(f"         latest: {sample}")
            except UsernameNotOccupiedError:
                print(f"    --   {channel:<28}  DOES NOT EXIST")
            except ChannelPrivateError:
                print(f"    --   {channel:<28}  PRIVATE")
            except FloodWaitError as e:
                wait = e.seconds + 5
                print(f"    !!   {channel:<28}  FLOOD WAIT {e.seconds}s, sleeping...")
                await asyncio.sleep(wait)
            except Exception as exc:
                print(f"    ??   {channel:<28}  {exc!s:.70}")

    await client.disconnect()
    print("\n  Telegram probe complete.")


# ── Main ──────────────────────────────────────────────────────────────────────


async def main() -> None:
    print(f"\nLive probe  {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")

    # RSS
    print_section("RSS — Habr Career (5 queries)")
    async with httpx.AsyncClient(headers=HTTP_HEADERS, follow_redirects=True) as s:
        for i, (label, url) in enumerate(RSS_SOURCES):
            if i:
                await asyncio.sleep(1.5)
            r = await probe_rss(s, label, url)
            icon = "OK  " if r["items"] > 0 else "FAIL"
            print(f"\n{icon} {r['label']}  [{r['items']} items]")
            if r["error"]:
                print(f"     ERROR: {r['error']}")
            for t in r["titles"]:
                print(f"     - {t}")

    # Career sites
    print_section("CAREER SITES — DNS + HTTP")
    async with httpx.AsyncClient(headers=HTTP_HEADERS, follow_redirects=True) as s:
        for company, candidates in CAREER_CANDIDATES:
            print(f"\n  [{company}]")
            live_found = False
            for label, url, method in candidates:
                await asyncio.sleep(0.7)
                r = await probe_url(s, label, url, method)
                status = r["status"]
                ok = isinstance(status, int) and 200 <= status < 400
                icon = "OK  " if ok else "    "
                flag = " <-- LIVE" if (ok and not live_found) else ""
                print(f"    {icon} {label:<40} {status}{flag}")
                if r["preview"]:
                    print(f"         {r['preview'][:90]}")
                if ok:
                    live_found = True

    # Telegram
    await probe_telegram()

    print(f"\nDone  {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}\n")


if __name__ == "__main__":
    asyncio.run(main())
