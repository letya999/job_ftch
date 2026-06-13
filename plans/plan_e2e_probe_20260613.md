# E2E Source Probe Plan — 2026-06-13

## Goal
Run a per-source diagnostic probe across all 90 sources listed by the user. For each source:
1. Attempt to fetch with a 15 s timeout
2. Capture the first error or item count
3. Classify result: OK | AUTH_REQUIRED | BOT_BLOCKED | PARSE_FAILED | NETWORK | CONFIG_ERROR

## Files to create

### 1. `config/sources_e2e_20260613.yaml`
Full sources file with all 90 sources mapped to their correct spec type.
See YAML content below.

### 2. `scripts/e2e_probe.py`
Standalone async diagnostic script:
- Loads `config/sources_e2e_20260613.yaml` via `build_composite_source_from_file`
- Iterates each child source individually (not composite — one at a time)
- Wraps fetch() with `asyncio.wait_for(timeout=15)`
- Catches and classifies exceptions:
  * `telethon.errors.*SessionPasswordNeededError` | `TelegramClient` auth → AUTH_REQUIRED
  * `asyncio.TimeoutError` → TIMEOUT
  * `aiohttp.ClientResponseError` status 403/429/503 → BOT_BLOCKED
  * `aiohttp.ClientConnectorError` | DNS → NETWORK
  * Any parsing exception from scrapers → PARSE_FAILED
  * Pydantic validation of spec → CONFIG_ERROR
- Collects at most 3 items before stopping fetch for that source
- Prints a formatted table at the end: source_name | type | status | note

## Sources YAML content

```yaml
# config/sources_e2e_20260613.yaml — E2E diagnostic probe, 2026-06-13
# All sources dry-run only. No auth secrets here.

sources:

  # ─── Russian employers (career sites) ───────────────────────────────────────
  - type: career_site
    url: https://yandex.ru/jobs
    source_name: yandex_jobs
    monitor: auto
    limit: 5

  - type: career_site
    url: https://www.tbank.ru/career/
    source_name: tbank_career
    monitor: auto
    limit: 5

  - type: career_site
    url: https://rabota.sber.ru
    source_name: sber_rabota
    monitor: auto
    limit: 5

  - type: career_site
    url: https://team.vk.company
    source_name: vk_team
    monitor: auto
    limit: 5

  - type: career_site
    url: https://career.avito.com
    source_name: avito_career
    monitor: auto
    limit: 5

  - type: career_site
    url: https://ozon.tech/vacancies/
    source_name: ozon_tech
    monitor: auto
    limit: 5

  - type: career_site
    url: https://job.mts.ru
    source_name: mts_job
    monitor: auto
    limit: 5

  - type: career_site
    url: https://careers.kaspersky.com
    source_name: kaspersky_careers
    monitor: auto
    limit: 5

  - type: career_site
    url: https://job.ptsecurity.com
    source_name: pt_security_job
    monitor: auto
    limit: 5

  - type: career_site
    url: https://tech.x5.ru/career
    source_name: x5_tech_career
    monitor: auto
    limit: 5

  # ─── Kazakhstan employers (career sites) ────────────────────────────────────
  - type: career_site
    url: https://kaspi.kz/guide/career/
    source_name: kaspi_career
    monitor: auto
    limit: 5

  - type: career_site
    url: https://freedomholdingcorp.com/careers
    source_name: freedom_holding_careers
    monitor: auto
    limit: 5

  - type: career_site
    url: https://people.beeline.kz
    source_name: beeline_kz
    monitor: auto
    limit: 5

  - type: career_site
    url: https://kolesa.group/career/job
    source_name: kolesa_group_career
    monitor: auto
    limit: 5

  - type: career_site
    url: https://halykbank.kz/about/career
    source_name: halyk_bank_career
    monitor: auto
    limit: 5

  - type: career_site
    url: https://careers.airastana.com
    source_name: air_astana_careers
    monitor: auto
    limit: 5

  - type: career_site
    url: https://choco.family/career
    source_name: choco_career
    monitor: auto
    limit: 5

  - type: career_site
    url: https://jobs.indrive.com
    source_name: indrive_jobs
    monitor: auto
    limit: 5

  - type: career_site
    url: https://btsdigital.kz
    source_name: bts_digital
    monitor: auto
    limit: 5

  - type: career_site
    url: https://sergek.com/career
    source_name: sergek_career
    monitor: auto
    limit: 5

  # ─── Russian-language job aggregators ────────────────────────────────────────
  # HH.ru — official REST API adapter
  - type: rest_api
    base_url: https://api.hh.ru/
    jobs_endpoint: vacancies
    params:
      text: "python developer"
      area: "1"
      per_page: "5"
    source_name: hh_ru
    pagination:
      type: page
      page_param: page
      page_size_param: per_page
      total_path: found
      items_path: items

  # HH.kz — same API, Kazakhstan area (area=159)
  - type: rest_api
    base_url: https://api.hh.ru/
    jobs_endpoint: vacancies
    params:
      text: "python developer"
      area: "159"
      per_page: "5"
    source_name: hh_kz
    pagination:
      type: page
      page_param: page
      page_size_param: per_page
      total_path: found
      items_path: items

  # Habr Career — RSS feed (AI/ML filter)
  - type: rss_feed
    feed_url: https://career.habr.com/vacancies/rss?q=machine+learning&type=1
    source_name: habr_career_ml
    incremental: false

  - type: career_site
    url: https://geekjob.ru
    source_name: geekjob
    monitor: auto
    limit: 5

  - type: career_site
    url: https://getmatch.ru
    source_name: getmatch
    monitor: auto
    limit: 5

  - type: career_site
    url: https://hirify.me
    source_name: hirify
    monitor: auto
    limit: 5

  - type: career_site
    url: https://finder.work
    source_name: finder_work
    monitor: auto
    limit: 5

  - type: career_site
    url: https://vcv.ru/jobs
    source_name: vcv_jobs
    monitor: auto
    limit: 5

  - type: career_site
    url: https://rabota.ru
    source_name: rabota_ru
    monitor: auto
    limit: 5

  - type: career_site
    url: https://www.superjob.ru
    source_name: superjob
    monitor: auto
    limit: 5

  # ─── Global aggregators ───────────────────────────────────────────────────────
  - type: career_site
    url: https://www.indeed.com
    source_name: indeed
    monitor: auto
    limit: 5

  - type: career_site
    url: https://www.glassdoor.com
    source_name: glassdoor
    monitor: auto
    limit: 5

  - type: career_site
    url: https://www.monster.com
    source_name: monster
    monitor: auto
    limit: 5

  - type: career_site
    url: https://www.ziprecruiter.com
    source_name: ziprecruiter
    monitor: auto
    limit: 5

  - type: career_site
    url: https://wellfound.com/jobs
    source_name: wellfound
    monitor: auto
    limit: 5

  - type: career_site
    url: https://www.dice.com
    source_name: dice
    monitor: auto
    limit: 5

  - type: career_site
    url: https://builtin.com/jobs
    source_name: builtin
    monitor: auto
    limit: 5

  - type: career_site
    url: https://www.levels.fyi/jobs
    source_name: levels_fyi
    monitor: auto
    limit: 5

  - type: career_site
    url: https://www.simplyhired.com
    source_name: simplyhired
    monitor: auto
    limit: 5

  - type: career_site
    url: https://www.careerbuilder.com
    source_name: careerbuilder
    monitor: auto
    limit: 5

  - type: career_site
    url: https://www.adzuna.com
    source_name: adzuna
    monitor: auto
    limit: 5

  - type: career_site
    url: https://www.reed.co.uk/jobs
    source_name: reed_uk
    monitor: auto
    limit: 5

  - type: career_site
    url: https://www.totaljobs.com
    source_name: totaljobs
    monitor: auto
    limit: 5

  - type: career_site
    url: https://www.jobserve.com
    source_name: jobserve
    monitor: auto
    limit: 5

  # RemoteOK — has JSON API
  - type: rest_api
    base_url: https://remoteok.com/
    jobs_endpoint: "api?tag=python"
    source_name: remoteok
    headers:
      User-Agent: "Mozilla/5.0 (compatible; job_ftch/1.0)"

  # We Work Remotely — RSS
  - type: rss_feed
    feed_url: https://weworkremotely.com/remote-jobs.rss
    source_name: weworkremotely
    incremental: false

  - type: career_site
    url: https://www.flexjobs.com
    source_name: flexjobs
    monitor: auto
    limit: 5

  - type: career_site
    url: https://jooble.org
    source_name: jooble
    monitor: auto
    limit: 5

  - type: career_site
    url: https://www.eurojobs.com
    source_name: eurojobs
    monitor: auto
    limit: 5

  - type: career_site
    url: https://jobs.google.com
    source_name: google_jobs
    monitor: auto
    limit: 5

  # ─── Telegram groups (discussion) ────────────────────────────────────────────
  - type: telegram_group
    entity: "@vibe_coding_community"
    source_name: tg_vibe_coding
    limit: 20
    auth_source_id: telegram

  - type: telegram_group
    entity: "@noflamenogame"
    source_name: tg_noflamenogame_group
    limit: 20
    auth_source_id: telegram

  - type: telegram_group
    entity: "@deordie_chat"
    source_name: tg_deordie_chat
    limit: 20
    auth_source_id: telegram

  - type: telegram_group
    entity: "@handlchatru"
    source_name: tg_handlchatru
    limit: 20
    auth_source_id: telegram

  - type: telegram_group
    entity: "@dsml_kz"
    source_name: tg_dsml_kz
    limit: 20
    auth_source_id: telegram

  - type: telegram_group
    entity: "@creatory"
    source_name: tg_creatory
    limit: 20
    auth_source_id: telegram

  - type: telegram_group
    entity: "@text2image"
    source_name: tg_text2image
    limit: 20
    auth_source_id: telegram

  - type: telegram_group
    entity: "@TGStat_Chat"
    source_name: tg_tgstat_chat
    limit: 20
    auth_source_id: telegram

  - type: telegram_group
    entity: "@neuraldeepchat"
    source_name: tg_neuraldeepchat
    limit: 20
    auth_source_id: telegram

  - type: telegram_group
    entity: "@ru_python"
    source_name: tg_ru_python
    limit: 20
    auth_source_id: telegram

  - type: telegram_group
    entity: "@it_chat_ru"
    source_name: tg_it_chat_ru
    limit: 20
    auth_source_id: telegram

  - type: telegram_group
    entity: "@devops_ru_chat"
    source_name: tg_devops_ru_chat
    limit: 20
    auth_source_id: telegram

  - type: telegram_group
    entity: "@mlopschat"
    source_name: tg_mlopschat
    limit: 20
    auth_source_id: telegram

  - type: telegram_group
    entity: "@langchain_russia"
    source_name: tg_langchain_russia
    limit: 20
    auth_source_id: telegram

  - type: telegram_group
    entity: "@llm_ru_chat"
    source_name: tg_llm_ru_chat
    limit: 20
    auth_source_id: telegram

  - type: telegram_group
    entity: "@genai_ru"
    source_name: tg_genai_ru
    limit: 20
    auth_source_id: telegram

  - type: telegram_group
    entity: "@data_engineers_ru"
    source_name: tg_data_engineers_ru
    limit: 20
    auth_source_id: telegram

  - type: telegram_group
    entity: "@datascience_ru_chat"
    source_name: tg_datascience_ru_chat
    limit: 20
    auth_source_id: telegram

  - type: telegram_group
    entity: "@ai_engineers_ru"
    source_name: tg_ai_engineers_ru
    limit: 20
    auth_source_id: telegram

  - type: telegram_group
    entity: "@ai_pm_ru"
    source_name: tg_ai_pm_ru
    limit: 20
    auth_source_id: telegram

  # ─── Telegram channels (broadcast) ───────────────────────────────────────────
  - type: telegram_channel
    entity: "@neuraldeep"
    source_name: tg_neuraldeep
    limit: 20
    auth_source_id: telegram

  - type: telegram_channel
    entity: "@aidaparen"
    source_name: tg_aidaparen
    limit: 20
    auth_source_id: telegram

  - type: telegram_channel
    entity: "@agi_and_rl"
    source_name: tg_agi_and_rl
    limit: 20
    auth_source_id: telegram

  - type: telegram_channel
    entity: "@elkornacio"
    source_name: tg_elkornacio
    limit: 20
    auth_source_id: telegram

  - type: telegram_channel
    entity: "@ethichlid"
    source_name: tg_ethichlid
    limit: 20
    auth_source_id: telegram

  - type: telegram_channel
    entity: "@AI4Dev"
    source_name: tg_ai4dev
    limit: 20
    auth_source_id: telegram

  - type: telegram_channel
    entity: "@deordie"
    source_name: tg_deordie
    limit: 20
    auth_source_id: telegram

  - type: telegram_channel
    entity: "@noflamenogame"
    source_name: tg_noflamenogame_chan
    limit: 20
    auth_source_id: telegram

  - type: telegram_channel
    entity: "@dsmlkz_news"
    source_name: tg_dsmlkz_news
    limit: 20
    auth_source_id: telegram

  - type: telegram_channel
    entity: "@data_events"
    source_name: tg_data_events
    limit: 20
    auth_source_id: telegram

  - type: telegram_channel
    entity: "@junior_pm"
    source_name: tg_junior_pm
    limit: 20
    auth_source_id: telegram

  - type: telegram_channel
    entity: "@ai_machinelearning_big_data"
    source_name: tg_ai_ml_bigdata
    limit: 20
    auth_source_id: telegram

  - type: telegram_channel
    entity: "@opensourceai"
    source_name: tg_opensourceai
    limit: 20
    auth_source_id: telegram

  - type: telegram_channel
    entity: "@llm4dev"
    source_name: tg_llm4dev
    limit: 20
    auth_source_id: telegram

  - type: telegram_channel
    entity: "@big_llm_course"
    source_name: tg_big_llm_course
    limit: 20
    auth_source_id: telegram

  - type: telegram_channel
    entity: "@data_secrets"
    source_name: tg_data_secrets
    limit: 20
    auth_source_id: telegram

  - type: telegram_channel
    entity: "@machinelearning_ru"
    source_name: tg_machinelearning_ru
    limit: 20
    auth_source_id: telegram

  - type: telegram_channel
    entity: "@ai_meetups"
    source_name: tg_ai_meetups
    limit: 20
    auth_source_id: telegram

  - type: telegram_channel
    entity: "@rodion_ai"
    source_name: tg_rodion_ai
    limit: 20
    auth_source_id: telegram

  - type: telegram_channel
    entity: "@senioraugur"
    source_name: tg_senioraugur
    limit: 20
    auth_source_id: telegram
```

## Diagnostic script `scripts/e2e_probe.py`

```python
"""E2E probe: test each source individually, classify success/error."""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Any

import yaml

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from job_ftch.infrastructure.sources.composite import CompositeSource

logging.basicConfig(level=logging.ERROR)  # suppress noise

TIMEOUT_S = 15
MAX_ITEMS = 3

ERROR_PATTERNS: list[tuple[str, str]] = [
    ("SessionPasswordNeededError", "AUTH_REQUIRED"),
    ("AuthKeyError", "AUTH_REQUIRED"),
    ("UserDeactivatedBanError", "AUTH_REQUIRED"),
    ("FloodWaitError", "RATE_LIMITED"),
    ("ChannelPrivateError", "AUTH_REQUIRED"),
    ("UsernameNotOccupiedError", "NOT_FOUND"),
    ("TimeoutError", "TIMEOUT"),
    ("ClientConnectorError", "NETWORK"),
    ("ClientResponseError", "HTTP_ERROR"),
    ("ConnectionRefusedError", "NETWORK"),
    ("gaierror", "NETWORK"),  # DNS failure
    ("403", "BOT_BLOCKED"),
    ("429", "RATE_LIMITED"),
    ("503", "BOT_BLOCKED"),
    ("CloudFlare", "BOT_BLOCKED"),
    ("Cloudflare", "BOT_BLOCKED"),
    ("ValidationError", "CONFIG_ERROR"),
    ("JobListNotFound", "PARSE_FAILED"),
    ("ParseError", "PARSE_FAILED"),
]


def classify_error(exc: Exception) -> str:
    exc_str = f"{type(exc).__name__}: {exc}"
    for pattern, category in ERROR_PATTERNS:
        if pattern in exc_str:
            return category
    return "UNKNOWN_ERROR"


async def probe_source(source: Any, source_name: str) -> dict[str, Any]:
    start = time.monotonic()
    items: list[Any] = []
    error_category: str | None = None
    error_detail: str = ""

    try:
        async def collect() -> None:
            async for item in source.fetch():
                items.append(item)
                if len(items) >= MAX_ITEMS:
                    break

        await asyncio.wait_for(collect(), timeout=TIMEOUT_S)
        status = "OK" if items else "EMPTY"
    except asyncio.TimeoutError:
        error_category = "TIMEOUT"
        error_detail = f">{TIMEOUT_S}s"
        status = "TIMEOUT"
    except Exception as exc:
        error_category = classify_error(exc)
        error_detail = str(exc)[:120]
        status = error_category

    elapsed = time.monotonic() - start
    return {
        "source": source_name,
        "status": status,
        "items": len(items),
        "elapsed": f"{elapsed:.1f}s",
        "error": error_detail,
    }


async def main() -> None:
    config_path = Path(__file__).parent.parent / "config" / "sources_e2e_20260613.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    specs = raw.get("sources", [])

    print(f"Probing {len(specs)} sources (timeout={TIMEOUT_S}s, max_items={MAX_ITEMS})\n")
    print(f"{'SOURCE':<35} {'TYPE':<20} {'STATUS':<18} {'ITEMS':>5} {'ELAPSED':>8}  NOTE")
    print("-" * 110)

    # Import builder to build individual sources
    from job_ftch.application.builder import build_source_from_spec
    from job_ftch.config import get_settings
    from job_ftch.infrastructure.auth.env_auth import EnvAuthProvider

    settings = get_settings()
    auth = EnvAuthProvider()

    results: list[dict[str, Any]] = []
    for spec_dict in specs:
        source_name = spec_dict.get("source_name", spec_dict.get("entity", "?"))
        source_type = spec_dict.get("type", "?")
        try:
            source = build_source_from_spec(spec_dict, auth=auth, settings=settings)
            result = await probe_source(source, source_name)
        except Exception as exc:
            result = {
                "source": source_name,
                "status": "CONFIG_ERROR",
                "items": 0,
                "elapsed": "0.0s",
                "error": str(exc)[:120],
            }

        results.append({**result, "type": source_type})
        note = result["error"] if result["status"] not in ("OK", "EMPTY") else ""
        print(
            f"{source_name:<35} {source_type:<20} {result['status']:<18} "
            f"{result['items']:>5} {result['elapsed']:>8}  {note}"
        )

    # Summary by category
    from collections import Counter
    counts = Counter(r["status"] for r in results)
    print("\n─── Summary ───────────────────────────────")
    for status, count in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {status:<20} {count:>3}")
    print(f"  {'TOTAL':<20} {len(results):>3}")


if __name__ == "__main__":
    asyncio.run(main())
```

## Implementation steps for Gemini

1. Write `config/sources_e2e_20260613.yaml` with the YAML content above (all 90 sources).
2. Check if `build_source_from_spec` exists in `job_ftch/application/builder.py`. If not, find the correct function to instantiate a single source from a spec dict. The composite builder uses `build_composite_source_from_file`; look for the internal per-spec factory used there.
3. Write `scripts/e2e_probe.py` using the correct builder function name found in step 2.
4. Run: `python scripts/e2e_probe.py 2>scripts/e2e_probe_stderr.txt | tee scripts/e2e_probe_results.txt`
5. Report the full output table and summary.

## Important: auth situation
- Telegram sources: will fail with AUTH_REQUIRED unless `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and a session file are configured in `.env`.
- career_site + global aggregators: may fail with BOT_BLOCKED (Cloudflare, 403).
- hh.ru API: public, no auth needed for basic search — should work.
- RSS feeds: public — should work.
- Do NOT skip sources that fail — report ALL results including failures.
