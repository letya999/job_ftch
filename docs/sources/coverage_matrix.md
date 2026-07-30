---
title: "Source Coverage Matrix"
description: "Operational guidance for the heavy career-site boards that were the main"
updated: 2026-07-27
---
# Source Coverage Matrix

Operational guidance for the heavy career-site boards that were the main
volume bottleneck during the 2026-07 audit. This document does **not** replace
runtime source health or source assessment; it records the intended default
strategy per board so operators do not have to reverse-engineer it from logs.

## Default fixture — AI engineers / vibe-coders / AI automation / AI managers (CIS)

`fixtures/sources/ai_jobs.json` is the canonical source list for testing and running
the pipeline against CIS-region roles: AI engineers, LLM engineers, vibe-coders,
AI automation engineers, AI product builders, and AI managers.

The fixture was expanded to 22 sources on 2026-07-29 to widen `hh.ru` and
`Habr Career` query coverage.

Load programmatically:

```python
from pathlib import Path
from job_ftch.application.source_loader import load_sources

sources = load_sources(Path("fixtures/sources/ai_jobs.json"))
```

| # | `source_name` | Type | URL / entity | Region focus |
|---|---|---|---|---|
| 1 | `ml_jobs_kz` | telegram_channel | `@ml_jobs_kz` | KZ/RU ML jobs |
| 2 | `remote_ai_jobs` | telegram_channel | `@remote_ai_jobs` | Remote, CIS-friendly |
| 3 | `forproducts` | telegram_channel | `@forproducts` | Product/AI builders |
| 4 | `gleb_pro_ai` | telegram_channel | `@gleb_pro_ai` | AI engineering community |
| 5 | `ai_engineers_guild` | telegram_group | `@ai_engineers_guild` | AI engineers, 700+ members |
| 6 | `geekjob_ru` | career_site | geekjob.ru/vacancies | RU tech market |
| 7 | `hh_ru_ml` | career_site | hh.ru (ML search, area=113) | RU nationwide |
| 8 | `hh_ru_llm` | career_site | hh.ru (LLM search, area=113) | RU nationwide |
| 9 | `hh_ru_ai_eng` | career_site | hh.ru (AI engineer search, area=113) | RU nationwide |
| 10 | `hh_ru_mlops` | career_site | hh.ru (MLOps search, area=113) | RU nationwide |
| 11 | `hh_kz_ml` | career_site | hh.kz (ML search) | KZ nationwide |
| 12 | `yandex_jobs_ml` | career_site | yandex.ru/jobs | RU Big Tech |
| 13 | `superjob_ru_ml` | career_site | superjob.ru | RU nationwide |
| 14 | `rabota_sber_ru` | career_site | rabota.sber.ru | RU — Sberbank group |
| 15 | `tbank_it` | career_site | tbank.ru/career/it | RU — T-Bank |
| 16 | `vk_careers` | career_site | team.vk.company/vacancy | RU Big Tech |
| 17 | `avito_careers` | career_site | career.avito.com/vacancies | RU — Avito |
| 18 | `habr_career_ml` | career_site | career.habr.com (ML search) | RU tech market |
| 19 | `habr_career_ds` | career_site | career.habr.com (Data Scientist) | RU tech market |
| 20 | `habr_career_llm` | career_site | career.habr.com (LLM search) | RU tech market |
| 21 | `kolesa_group` | career_site | kolesa.group/career/job | KZ — Kolesa Group |
| 22 | `hirify_me` | career_site | hirify.me/jobs-in-russia | Russia AI/ML engineering market |

### Smoke test with this fixture

```bash
cat > /tmp/ai-jobs-smoke.yaml <<'EOF'
tenant_id: ai_jobs_smoke
display_name: AI Jobs CIS Smoke
sources: []
output:
  backend: json_file
  path: artifacts/ai_jobs_smoke/jobs.json
  jsonl: false
  schema_version: job_ftch.job_record.v1
EOF
uv run python - <<'PY'
import asyncio
from pathlib import Path
from job_ftch.application.source_loader import load_sources
from job_ftch.application.builder import configure

sources = load_sources(Path("fixtures/sources/ai_jobs.json"))
builder = configure(Path("/tmp/ai-jobs-smoke.yaml"))
builder.sources(sources)
asyncio.run(builder.run_async())
PY
```

## Boards

| Board | Current support in code | Recommended strategy | Browser required | Notes |
|---|---|---|---|---|
| `hh.ru` | `site_parsers/hh.py`, generic career-site stack | `api_sniffer -> dom` | Usually no for simple listings; yes when anti-bot/render escalates | Stable vacancy URLs, generic monitor stack with adaptive bypass. Official API exists as a separate `hh_api` source family. |
| `rabota.by` | `site_parsers/rabota.py`, generic career-site stack | `dom -> api_sniffer` | Sometimes | Treated as HH-like board with stable vacancy URLs. Use generic monitor stack first; escalate only when the listing page blocks or hides inventory. |
| `djinni.co/jobs/` | `site_parsers/djinni.py`, generic career-site stack | `dom` | Usually no | Stable detail URLs under `/jobs/<id>-<slug>/`. Start with plain DOM collection; only escalate if anti-bot or empty inventory is observed. |
| `jobs.dou.ua` | `site_parsers/dou.py`, generic career-site stack | `dom` | No in the normal case | Stable detail URLs and visible publish dates/RSS surface. Good candidate for time-window ingest without a browser. |
| `ozon.tech/vacancies` | `site_parsers/ozon.py`, generic career-site stack | `dom(render=true) -> browser fallback` | Yes | SPA-style board. Runtime defaults already request `render=true`. If Playwright Chromium is missing in the runtime image, disable the source instead of treating it as a normal failing board. |

## Operator rules

1. Prefer the generic career-site stack plus site defaults over custom parsers unless the board is a proven SPA/API-only outlier.
2. Treat `browser_required=true` in `/sources` output as an environment dependency, not as a content failure.
3. If a board needs Chromium and the deployment image does not ship it, disable that source explicitly until the image is upgraded.
4. Use `hh_api` or other first-party API sources when available and product-acceptable; they are lower-risk than browser scraping.
5. Revisit this matrix when a board changes its URL shape, starts blocking aggressively, or gains a better first-party API path.
