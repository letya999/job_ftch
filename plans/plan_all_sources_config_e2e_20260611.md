# Build comprehensive all-sources config and run e2e

## Context

**Worktree:** `C:/Users/User/a_projects/job_ftch_p2325`
**Branch:** `feature/parsers-phases-23-25`

The user wants ONE config file covering all 90+ sources below, with adaptive auto strategy,
safe Telegram rate limiting (personal account), and then an e2e run against all sources.

## Goal

Create `config/all_sources_full.yaml` in the worktree at `C:/Users/User/a_projects/job_ftch_p2325/config/`.

Then run the pipeline e2e:
```
cd C:/Users/User/a_projects/job_ftch_p2325
uv run python -m job_ftch pipeline \
  --sources-file config/all_sources_full.yaml \
  --dry-run \
  --once \
  --output-path output/e2e_full_20260611.jsonl \
  --jsonl
```

Print the output (item counts per source, errors) to stdout.

## Telegram safety rules (CRITICAL - personal account)

Telegram floods personal accounts with FloodWait if > ~15 requests/min.

For ALL telegram_channel entries:
- `limit: 30`   (fetch at most 30 posts)
- `interval_seconds: 300`  (poll no more than once per 5 min)

For ALL telegram_comments entries (paired after each channel):
- `post_limit: 30`
- `comment_limit_per_post: 30`

For ALL telegram_group entries:
- `limit: 50`
- `interval_seconds: 300`

Do NOT add Telegram sources without these limits.

## Config file structure

`config/all_sources_full.yaml` must have a top-level `sources:` list.

### Section 1: 20 Telegram Channels (+ comments pairs)

Each channel gets a `telegram_channel` entry immediately followed by a `telegram_comments` entry
with the same entity slug. Extract the slug from the t.me URL (the part after t.me/).

Channels to add:
```
neuraldeep        (https://t.me/neuraldeep)
aidaparen         (https://t.me/aidaparen)
agi_and_rl        (https://t.me/agi_and_rl)
elkornacio        (https://t.me/elkornacio)
ethichlid         (https://t.me/ethichlid)
AI4Dev            (https://t.me/AI4Dev)
deordie           (https://t.me/deordie)
noflamenogame     (https://t.me/noflamenogame)
dsmlkz_news       (https://t.me/dsmlkz_news)
data_events       (https://t.me/data_events)
junior_pm         (https://t.me/junior_pm)
ai_machinelearning_big_data  (https://t.me/ai_machinelearning_big_data)
opensourceai      (https://t.me/opensourceai)
llm4dev           (https://t.me/llm4dev)
big_llm_course    (https://t.me/big_llm_course)
data_secrets      (https://t.me/data_secrets)
machinelearning_ru (https://t.me/machinelearning_ru)
ai_meetups        (https://t.me/ai_meetups)
rodion_ai         (https://t.me/rodion_ai)
senioraugur       (https://t.me/senioraugur)
```

Example pair (repeat for each channel):
```yaml
- type: telegram_channel
  entity: neuraldeep
  limit: 30
  interval_seconds: 300

- type: telegram_comments
  entity: neuraldeep
  post_limit: 30
  comment_limit_per_post: 30
```

### Section 2: 20 Telegram Groups

```
vibe_coding_community
noflamenogame
deordie_chat
handlchatru
dsml_kz
creatory
text2image
TGStat_Chat
neuraldeepchat
ru_python
it_chat_ru
devops_ru_chat
mlopschat
langchain_russia
llm_ru_chat
genai_ru
data_engineers_ru
datascience_ru_chat
ai_engineers_ru
ai_pm_ru
```

Example:
```yaml
- type: telegram_group
  entity: vibe_coding_community
  limit: 50
  interval_seconds: 300
```

### Section 3: RU/KZ job aggregators (career_site, adaptive)

All career site sources use:
```yaml
  monitor: auto
  bypass: auto
  limit: 30
```

Sources (use AI/ML search queries in URLs where possible):

```yaml
# HeadHunter RU
- type: career_site
  url: "https://hh.ru/search/vacancy?text=machine+learning+engineer&area=113"
  source_name: hh_mle_ru
  monitor: auto
  bypass: auto
  limit: 30

- type: career_site
  url: "https://hh.ru/search/vacancy?text=LLM+engineer&area=113"
  source_name: hh_llm_ru
  monitor: auto
  bypass: auto
  limit: 30

# HeadHunter KZ
- type: career_site
  url: "https://hh.kz/search/vacancy?text=machine+learning"
  source_name: hh_kz_ml
  monitor: auto
  bypass: auto
  limit: 30

# Habr Career
- type: career_site
  url: "https://career.habr.com/vacancies?q=machine+learning"
  source_name: habr_ml
  monitor: auto
  bypass: auto
  limit: 30

# GeekJob (skip_ssl due to expired cert)
- type: career_site
  url: https://geekjob.ru/vacancies
  source_name: geekjob
  monitor: auto
  bypass: auto
  monitor_config:
    skip_ssl: true
  scraper_config:
    skip_ssl: true
  limit: 30

# GetMatch
- type: career_site
  url: "https://getmatch.ru/vacancies?sp=data+scientist"
  source_name: getmatch_ds
  monitor: auto
  bypass: auto
  limit: 30

# Hirify
- type: career_site
  url: https://hirify.me/jobs-in-russia
  source_name: hirify_ru
  monitor: auto
  bypass: auto
  limit: 30

# Finder.work
- type: career_site
  url: "https://finder.work/vacancies?q=machine+learning"
  source_name: finder_work_ml
  monitor: auto
  bypass: auto
  limit: 30

# VCV
- type: career_site
  url: "https://vcv.ru/jobs?q=machine+learning"
  source_name: vcv_ml
  monitor: auto
  bypass: auto
  limit: 30

# Rabota.ru
- type: career_site
  url: "https://rabota.ru/vakansii/machine-learning"
  source_name: rabota_ru_ml
  monitor: auto
  bypass: auto
  limit: 30

# SuperJob
- type: career_site
  url: "https://www.superjob.ru/vakansii/machine-learning-inzhener.html"
  source_name: superjob_ml
  monitor: auto
  bypass: auto
  limit: 30
```

### Section 4: RU large employers (AI-focused career pages)

```yaml
# Yandex
- type: career_site
  url: "https://yandex.ru/jobs/vacancies?department=machine+learning"
  source_name: yandex_ml
  monitor: auto
  bypass: auto
  limit: 30

# T-Bank
- type: career_site
  url: "https://www.tbank.ru/career/it/"
  source_name: tbank_it
  monitor: auto
  bypass: auto
  limit: 30

# Sber
- type: career_site
  url: "https://rabota.sber.ru/search?q=machine+learning"
  source_name: sber_ml
  monitor: auto
  bypass: auto
  limit: 30

# VK
- type: career_site
  url: https://team.vk.company/vacancy/
  source_name: vk_careers
  monitor: auto
  bypass: auto
  limit: 30

# Avito
- type: career_site
  url: https://career.avito.com/vacancies/
  source_name: avito_career
  monitor: auto
  bypass: auto
  limit: 30

# Ozon Tech
- type: career_site
  url: https://ozon.tech/vacancies/
  source_name: ozon_tech
  monitor: auto
  bypass: auto
  limit: 30

# MTS
- type: career_site
  url: "https://job.mts.ru/vacancies?q=machine+learning"
  source_name: mts_ml
  monitor: auto
  bypass: auto
  limit: 30

# Kaspersky
- type: career_site
  url: "https://careers.kaspersky.com/en/jobs/?category=ai-ml"
  source_name: kaspersky_ml
  monitor: auto
  bypass: auto
  limit: 30

# Positive Technologies
- type: career_site
  url: https://job.ptsecurity.com
  source_name: pt_security
  monitor: auto
  bypass: auto
  limit: 30

# X5 Tech
- type: career_site
  url: "https://tech.x5.ru/career"
  source_name: x5_tech_career
  monitor: auto
  bypass: auto
  limit: 30
```

### Section 5: KZ large employers

```yaml
# Kaspi
- type: career_site
  url: https://kaspi.kz/guide/career/
  source_name: kaspi_career
  monitor: auto
  bypass: auto
  limit: 30

# Freedom Holding
- type: career_site
  url: https://freedomholdingcorp.com/careers
  source_name: freedom_careers
  monitor: auto
  bypass: auto
  limit: 30

# Beeline KZ (OutSystems SPA - needs browser)
- type: career_site
  url: https://people.beeline.kz/
  source_name: beeline_kz
  monitor: auto
  bypass: auto
  limit: 30

# Kolesa Group
- type: career_site
  url: https://kolesa.group/career/job
  source_name: kolesa_career
  monitor: auto
  bypass: auto
  limit: 30

# Halyk Bank
- type: career_site
  url: https://halykbank.kz/about/career
  source_name: halyk_career
  monitor: auto
  bypass: auto
  limit: 30

# Air Astana
- type: career_site
  url: https://careers.airastana.com
  source_name: airastana_career
  monitor: auto
  bypass: auto
  limit: 30

# Choco
- type: career_site
  url: https://choco.family/career
  source_name: choco_career
  monitor: auto
  bypass: auto
  limit: 30

# inDrive
- type: career_site
  url: "https://jobs.indrive.com/?department=engineering"
  source_name: indrive_eng
  monitor: auto
  bypass: auto
  limit: 30

# BTS Digital
- type: career_site
  url: https://btsdigital.kz
  source_name: btsdigital
  monitor: auto
  bypass: auto
  limit: 30

# Sergek Group
- type: career_site
  url: https://sergek.com/career
  source_name: sergek_career
  monitor: auto
  bypass: auto
  limit: 30
```

### Section 6: Global aggregators

Use AI/ML/remote search queries in URLs where possible.

```yaml
# Indeed (search ML engineer, remote/worldwide)
- type: career_site
  url: "https://www.indeed.com/jobs?q=machine+learning+engineer&remotejobs=1"
  source_name: indeed_ml_remote
  monitor: auto
  bypass: auto
  limit: 30

# Glassdoor
- type: career_site
  url: "https://www.glassdoor.com/Job/machine-learning-engineer-jobs-SRCH_KO0,26.htm"
  source_name: glassdoor_ml
  monitor: auto
  bypass: auto
  limit: 30

# Monster
- type: career_site
  url: "https://www.monster.com/jobs/search?q=machine+learning+engineer&where=remote"
  source_name: monster_ml
  monitor: auto
  bypass: auto
  limit: 30

# ZipRecruiter
- type: career_site
  url: "https://www.ziprecruiter.com/jobs-search?search=machine+learning+engineer&location=remote"
  source_name: ziprecruiter_ml
  monitor: auto
  bypass: auto
  limit: 30

# Wellfound (AngelList)
- type: career_site
  url: "https://wellfound.com/jobs?role=ml-engineer"
  source_name: wellfound_ml
  monitor: auto
  bypass: auto
  limit: 30

# Dice
- type: career_site
  url: "https://www.dice.com/jobs?q=machine+learning+engineer&location=remote"
  source_name: dice_ml
  monitor: auto
  bypass: auto
  limit: 30

# Built In
- type: career_site
  url: "https://builtin.com/jobs/machine-learning"
  source_name: builtin_ml
  monitor: auto
  bypass: auto
  limit: 30

# Levels.fyi Jobs
- type: career_site
  url: "https://www.levels.fyi/jobs?jobFamily=Machine+Learning+Engineer"
  source_name: levelsfyi_ml
  monitor: auto
  bypass: auto
  limit: 30

# SimplyHired
- type: career_site
  url: "https://www.simplyhired.com/search?q=machine+learning+engineer&l=remote"
  source_name: simplyhired_ml
  monitor: auto
  bypass: auto
  limit: 30

# CareerBuilder
- type: career_site
  url: "https://www.careerbuilder.com/jobs?keywords=machine+learning+engineer"
  source_name: careerbuilder_ml
  monitor: auto
  bypass: auto
  limit: 30

# Adzuna
- type: career_site
  url: "https://www.adzuna.com/search?q=machine+learning+engineer"
  source_name: adzuna_ml
  monitor: auto
  bypass: auto
  limit: 30

# Reed (UK)
- type: career_site
  url: "https://www.reed.co.uk/jobs/machine-learning-engineer-jobs"
  source_name: reed_ml
  monitor: auto
  bypass: auto
  limit: 30

# TotalJobs (UK)
- type: career_site
  url: "https://www.totaljobs.com/jobs/machine-learning-engineer"
  source_name: totaljobs_ml
  monitor: auto
  bypass: auto
  limit: 30

# JobServe
- type: career_site
  url: "https://www.jobserve.com/gb/en/JobSearch.aspx?shid=1EB4F5A6EF2E7634DB0B&q=machine+learning"
  source_name: jobserve_ml
  monitor: auto
  bypass: auto
  limit: 30

# RemoteOK
- type: career_site
  url: "https://remoteok.com/remote-machine-learning-jobs"
  source_name: remoteok_ml
  monitor: auto
  bypass: auto
  limit: 30

# We Work Remotely
- type: career_site
  url: "https://weworkremotely.com/categories/remote-programming-jobs"
  source_name: wwr_programming
  monitor: auto
  bypass: auto
  limit: 30

# FlexJobs
- type: career_site
  url: "https://www.flexjobs.com/search?search=machine+learning+engineer&location=anywhere"
  source_name: flexjobs_ml
  monitor: auto
  bypass: auto
  limit: 30

# Jooble (international ML jobs)
- type: career_site
  url: "https://jooble.org/jobs-machine-learning-engineer"
  source_name: jooble_ml
  monitor: auto
  bypass: auto
  limit: 30

# EuroJobs
- type: career_site
  url: "https://www.eurojobs.com/search-results/?search%5Bkeyword%5D=machine+learning"
  source_name: eurojobs_ml
  monitor: auto
  bypass: auto
  limit: 30

# Google Jobs (via search)
- type: career_site
  url: "https://jobs.google.com/search?q=machine+learning+engineer&location=remote"
  source_name: google_jobs_ml
  monitor: auto
  bypass: auto
  limit: 30
```

## Steps

### Step 1: Write the config file [x]

Write `C:/Users/User/a_projects/job_ftch_p2325/config/all_sources_full.yaml` with all sections
above combined under a single `sources:` key. Follow the exact YAML structure from the examples.

Add a comment header at the top:
```yaml
# all_sources_full.yaml — comprehensive 90+ source config
# Telegram: 20 channels (+ comments) + 20 groups, rate-limited for personal account
# Career sites: RU/KZ employers + aggregators + global aggregators
# monitor: auto + bypass: auto everywhere (adaptive escalation)
```

### Step 2: Validate YAML [x]

Run:
```powershell
cd C:/Users/User/a_projects/job_ftch_p2325
uv run python -c "import yaml; yaml.safe_load(open('config/all_sources_full.yaml'))"
```

Fix any YAML syntax errors.

### Step 3: Run e2e [ ]

```powershell
cd C:/Users/User/a_projects/job_ftch_p2325
New-Item -ItemType Directory -Force -Path output | Out-Null
uv run python -m job_ftch pipeline `
  --sources-file config/all_sources_full.yaml `
  --dry-run `
  --once `
  --output-path output/e2e_full_20260611.jsonl `
  --jsonl `
  --max-items 500 2>&1
```

Capture all stdout+stderr. The run will go through all sources sequentially.

**Expected results:**
- Telegram sources: items extracted or "0 items" (channels may be empty/slow)
- Career sites: at least some sites should return items; some will timeout or get 0 (acceptable)
- No Python exceptions / tracebacks for sources that are properly handled
- FloodWait errors from Telegram are acceptable (shows rate limiting working)

### Step 4: Summary

After the run completes, print:
- Total items extracted
- Per-source item counts (if available in output)
- Any sources that failed with errors (list them)
- Telegram sources: note any FloodWait or similar

### Step 5: Commit

```powershell
cd C:/Users/User/a_projects/job_ftch_p2325
git add config/all_sources_full.yaml
git commit -m "feat(config): add comprehensive all-sources config (20ch+20grp+40career+20global)"
```

Do NOT commit output/ files.

## Important notes

- Work in `C:/Users/User/a_projects/job_ftch_p2325` (the feature branch worktree)
- Do NOT modify any existing .py files - config-only change
- Do NOT push
- If an individual source errors during e2e, that is fine - report it but continue
- The whole run may take 10-20 minutes for career sites; Telegram sources come first and are fast
