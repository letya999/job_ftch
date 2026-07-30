"""Apply non-evasive runtime source fixes for known career-site failures."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_ftch.application.registry import create_store, load_extensions
from job_ftch.application.tenant_store import TenantStore
from job_ftch.config import get_settings

PATCHES = {
    "career_site:kz_bereke": {
        "url": "https://hh.kz/search/vacancy?from=employerPage&employer_id=1245405",
    },
    "career_site:kz_kcell_jobs": {
        "monitor": "dom",
        "monitor_config": {
            "render": True,
            "wait": "networkidle",
            "url_filter": r"jobs\.kcell\.kz/job/\d+",
        },
    },
    "career_site:ru_hirehi": {
        "monitor": "dom",
        "monitor_config": {
            "url_filter": r"hirehi\.ru/[a-z0-9-]+/[a-z0-9-]+-\d+$",
        },
    },
    "career_site:ru_ntechlab": {
        "url": "https://hh.ru/search/vacancy?from=employerPage&employer_id=2328032",
    },
    "career_site:ru_ozon_tech": {
        "url": "https://ozon.tech/vacancies/",
        "monitor": "dom",
        "monitor_config": {
            "render": True,
            "wait": "domcontentloaded",
            "wait_fallback": "networkidle",
            "challenge_retries": 2,
            "settle_seconds": 2,
            "url_filter": r"ozon\.tech/vacancies/[a-f0-9\-]+-[a-z0-9-]+/?$",
        },
    },
    "career_site:ru_rabota_ru": {
        "monitor": "dom",
        "monitor_config": {
            "render": True,
            "wait": "domcontentloaded",
            "settle_seconds": 1,
            "url_filter": r"www\.rabota\.ru/vacancy/[^/?#]+/?$",
        },
    },
    "career_site:www_rabota_ru_vacancy": {
        "monitor": "dom",
        "monitor_config": {
            "render": True,
            "wait": "domcontentloaded",
            "settle_seconds": 1,
            "url_filter": r"www\.rabota\.ru/vacancy/[^/?#]+/?$",
        },
    },
    "career_site:ru_superjob": {
        "bypass": "cloak",
        "monitor": "dom",
        "monitor_config": {
            "render": True,
            "wait": "domcontentloaded",
            "settle_seconds": 1,
            "url_filter": r"www\.superjob\.ru/vakansii/[^/?#]+-\d+\.html$",
        },
    },
    "career_site:www_superjob_ru_vakansii": {
        "bypass": "cloak",
        "monitor": "dom",
        "monitor_config": {
            "render": True,
            "wait": "domcontentloaded",
            "settle_seconds": 1,
            "url_filter": r"www\.superjob\.ru/vakansii/[^/?#]+-\d+\.html$",
        },
    },
    "career_site:ru_vk_careers": {
        "monitor": "dom",
        "monitor_config": {
            "render": True,
            "wait": "domcontentloaded",
            "settle_seconds": 1,
            "url_filter": r"team\.vk\.company/vacancy/\d+/?$",
        },
    },
    "career_site:team_vk_company_vacancy": {
        "monitor": "dom",
        "monitor_config": {
            "render": True,
            "wait": "domcontentloaded",
            "settle_seconds": 1,
            "url_filter": r"team\.vk\.company/vacancy/\d+/?$",
        },
    },
    "career_site:ru_yandex_jobs": {
        "monitor_config": {
            "wait": "networkidle",
            "wait_fallback": "domcontentloaded",
            "challenge_retries": 2,
            "locale": "ru-RU",
            "warmup_url": "https://yandex.ru/company/",
        },
    },
    "career_site:yandex_ru_jobs": {
        "monitor_config": {
            "wait": "networkidle",
            "wait_fallback": "domcontentloaded",
            "challenge_retries": 2,
            "locale": "ru-RU",
            "warmup_url": "https://yandex.ru/company/",
        },
    },
}


async def main() -> None:
    load_extensions()
    raw_store = create_store(get_settings())
    if hasattr(raw_store, "initialize"):
        await raw_store.initialize()
    store = TenantStore("ai_jobs", raw_store)

    for source_id, patch in PATCHES.items():
        record = await store.get_runtime_source(source_id)
        if record is None:
            print(f"{source_id}: missing")
            continue
        new_spec = record.spec.model_copy(update=patch)
        await store.save_runtime_source(record.model_copy(update={"spec": new_spec}))
        print(f"{source_id}: updated")


if __name__ == "__main__":
    asyncio.run(main())
