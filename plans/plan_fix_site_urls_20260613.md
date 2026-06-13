# Fix site source URLs in config/sources_e2e_20260613.yaml — 2026-06-13

## Context
Per-site live probing identified 3 career_site sources with WRONG/DEAD urls that work after
correction, and 7 sources that are hard-blocked (anti-bot) or unparseable SPAs. This is a
CONFIG-ONLY change to `config/sources_e2e_20260613.yaml`. Do NOT touch any Python code.

## Task 1 — Correct 3 broken URLs (verified working after change)
In `config/sources_e2e_20260613.yaml`, change ONLY the `url:` value for these three sources
(keep source_name, monitor: auto, limit unchanged):

1. source_name `pt_security_job`:
   url FROM `https://job.ptsecurity.com` TO `https://www.ptsecurity.com/ru-ru/about/vacancy/`
2. source_name `indrive_jobs`:
   url FROM `https://jobs.indrive.com` TO `https://careers.indrive.com`
3. source_name `sergek_career`:
   url FROM `https://sergek.com/career` TO `https://sergek.com`

## Task 2 — Annotate the 7 hard-blocked / unparseable sources
For each of these sources add a comment line directly ABOVE the `- type: career_site` entry,
stating the verified reason. Do NOT delete them, do NOT change their url. Keep them in the file.
Add exactly one comment line per source:

- `yandex_jobs`     → `# BLOCKED 2026-06-13: HTTP 429 anti-bot on all bypass tiers (noop/curl_stealth/stealth/cloak)`
- `ozon_tech`       → `# BLOCKED 2026-06-13: HTTP 403 anti-bot on all bypass tiers`
- `kaspi_career`    → `# BLOCKED 2026-06-13: SPA, api_sniffer no useful feed, dom timeout`
- `x5_tech_career`  → `# BROKEN 2026-06-13: tech.x5.ru DNS fail; x5.ru/career SPA categories unparseable by current scrapers`
- `choco_career`    → `# BROKEN 2026-06-13: choco.family DNS fail; chocofamily.kz/choco.kz SPA yield nothing`
- `air_astana_careers` → `# BROKEN 2026-06-13: careers.airastana.com DNS fail; airastana.com candidates empty/timeout`
- `freedom_holding_careers` → `# BROKEN 2026-06-13: /careers 404; freedomfinance.kz redirects to fbroker.kz 404`

## Acceptance criteria
1. The 3 urls in Task 1 are updated to the new values exactly.
2. The 7 comment lines from Task 2 are present above the right sources.
3. No Python files changed. `git diff --name-only` lists ONLY config/sources_e2e_20260613.yaml.
4. YAML still parses (valid). No other source entries modified.

## Out of scope
- Any code change. Fixing the 7 hard-blocked sources (needs managed scraper or new monitor).
- Telegram sources. Other config files.
