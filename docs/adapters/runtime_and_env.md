---
title: "Runtime и env: где правда"
description: "Короткая карта того, какие файлы являются source of truth для конфигурации и переменных окружения."
updated: 2026-11-20
---
# Runtime и env: где правда

Отдельные длинные документы про `configuration` и `env_reference` удалены,
потому что они слишком легко начинали дублировать реальные файлы.

Здесь оставлен только короткий навигатор по источникам правды.

## Где смотреть конфигурацию

- `job_ftch/config.py` — полный набор `Settings`, дефолты, валидация и порядок источников.
- `config/runtime.yaml` — общая runtime-база.
- `config/runtime.dev.yaml` — dev-override.
- `config/runtime.prod.yaml` — prod-override.
- `config/runtime.cliproxy.yaml` — legacy overlay; глобальный gateway не переключает именованные ETL bindings.
- `docs/operations/cliproxy-vps.md` — Compose sidecar, loopback ports, Google OAuth и подключение к bot.
- `docs/operations/llm-queue-and-home-egress.md` — optional PostgreSQL queue, shared quota scopes and Android home egress.
- `config/runtime.llm_queue.yaml` и `config/runtime.home_egress.yaml` — полный CLIProxy profile overlay; оба задают основной HTTP timeout 360 секунд.
- CLIProxy-only run sessions, jitter, classified deferred states and optional aggregate reports require an explicit `gateway: cliproxy` queued profile (ADR 107); direct compatible requests retain their previous behavior.
- `job_ftch/adapters/telegram_bot/runtime.dev.yaml` — bot-специфичный dev runtime.
- `job_ftch/adapters/telegram_bot/runtime.prod.yaml` — bot-специфичный prod runtime; текущие ETL и CAPTCHA bindings используют оплаченный OpenAI API, CLIProxy profile остаётся опциональным.
- `job_ftch/adapters/telegram_bot/config/tenants/*.yaml` — tenant-level sources и wiring.
- `scripts/check_config_layers.py` — машинная проверка границ между tenant и runtime слоями.

## Где смотреть env

- `.env.dev.example`
- `.env.prod.example`
- `.env.cliproxy.example` — локальный CLIProxyAPI: `JOB_FTCH_OPENAI_BASE_URL=http://127.0.0.1:8317/v1`
- Image captcha OCR fallback: `JOB_FTCH_CAPTCHA_VISION_BASE_URL` / `JOB_FTCH_CAPTCHA_VISION_MODEL`. Auth is `JOB_FTCH_OPENAI_API_KEY` (or `OPENAI_API_KEY`). Это не `JOB_FTCH_LLM_GATEWAY`.
- LLM preflight/quota guard: `JOB_FTCH_LLM_PREFLIGHT_ENABLED`, `JOB_FTCH_LLM_PREFLIGHT_TIMEOUT_SECONDS`, `JOB_FTCH_LLM_QUOTA_RETRY_DELAY_SECONDS`, `JOB_FTCH_LLM_QUOTA_MAX_RETRIES`.
- Local/dev captcha allowlist: `JOB_FTCH_CAPTCHA_AUTHORIZED_DOMAINS=*` (see `.env.dev.example` and `config/runtime.dev.yaml`). Empty env denies every domain and overrides YAML. Prod template stays empty.
- `job_ftch/adapters/telegram_bot/.env.dev.example`
- `job_ftch/adapters/telegram_bot/.env.prod.example`
- `deploy/observability/.env.dev.example`
- `deploy/observability/.env.prod.example`

Это и есть актуальные шаблоны переменных окружения.

## Порядок применения

`Settings` читает `.env` плюс `.env.dev` или `.env.prod` в зависимости от
`JOB_FTCH_ENV`; реальные переменные окружения имеют приоритет над dotenv.
Runtime YAML читается через `JOB_FTCH_RUNTIME_CONFIG_PATH`; compose для bot
задаёт цепочку `config/runtime.yaml`, env-specific root runtime и
`job_ftch/adapters/telegram_bot/runtime*.yaml`.

## Residential proxy rescue

Residential proxies are a paid rescue tier, not the default route for every
career site. The root `.env*.example` files contain a DataImpulse RU gateway
profile (`gw.dataimpulse.com:823`) with 1 GB total per-process budget,
0.05 GB per-domain budget, allow-domains for Habr/Higgsfield/EPAM rescue sources, and
deny-domains for banking/government targets. An empty
`JOB_FTCH_PROXY_RESCUE_ALLOW_DOMAINS` denies every domain; `*` allows all
(deny-list still wins). Real `JOB_FTCH_PROXY_USER` and
`JOB_FTCH_PROXY_PASS` values must live only in local/compose env files.

## Практическое правило

- Секреты, токены, DSN и URL ищите в `.env*.example`.
- Политику пайплайна, модели, пороги, concurrency и budgets ищите в `config/runtime*.yaml`.
- Bot-specific auth, tenant directory и compose wiring ищите в
  `job_ftch/adapters/telegram_bot/*`.
- Operational telemetry env лежит отдельно в `deploy/observability/*`.
- Что именно существует и как резолвится, проверяйте по `job_ftch/config.py`, а не по prose.

## Аудитория вакансий

В YAML тенанта `publication_policies` связывает правила с получателями. Ключ
`site` применяется к сайту и публичному API, точное имя канала — к отправке в
Telegram, `chat` — к ручной выдаче в чате. Ключ `"*"` задаёт резервное правило.
Тенант без этих записей сохраняет текущую аудиторию.

```yaml
publication_policies:
  site:
    eligible_countries: [KZ, AM, KG]
    posting_languages: [ru]
    company_languages: [ru]
    match: any
    insufficient_evidence: review
    shadow: true
    verified_employers:
      Example:
        languages: [ru]
        source: https://example.org/careers
        checked_on: 2026-11-25
```

Список стран задаётся кодами ISO, языки — строчными двухбуквенными кодами.
`match: any` пропускает вакансию при одном подтверждённом совпадении,
`match: all` требует все заданные условия. `require_country: true` делает
географию обязательной и применяет any/all к оставшимся условиям.
`country_basis: eligibility` учитывает ограничения места найма. Режим
`office` проверяет географию офисных и гибридных позиций. Русский текст сам по
себе подтверждает только язык объявления. Рабочий язык компании должен быть
заявлен в описании или в проверенном редактором реестре работодателей.
Параметр `accept_team_language: true` также принимает явно указанный рабочий язык команды.

Для объявления «Лимасол или Дубай» Telegram показывает обе страны.
У явно указанной территории найма появляются теги `#глобал`, `#европа`,
`#ес`, `#снг`, `#emea`, `#apac` или `#latam`. Один тег `#remote` описывает формат работы.
Для фильтра по странам состав территории задаёт `region_countries` в политике
получателя. Например, `region_countries: {CIS: [AM, KZ]}` трактует слово CIS
как эти две страны в рамках данного правила. При неопределённом составе
региона решение по географии остаётся неизвестным. Явный найм по всему миру
включает выбранные страны, а оговорки об исключениях проверяются отдельно.
Словарь `language_aliases: {pl: [Polish, польский]}` добавляет варианты
названия языка в заявлениях о рабочем языке. Условия отбора задают
`eligible_countries`, `posting_languages` и `company_languages`.

Грейды берутся из названия вакансии и явно указанных полей источника.
`Middle–Senior+` даёт `#middle #senior`, а годы стажа сами по себе
оставляют грейд пустым. Совмещённая должность `Developer / Analyst` даёт
`#разработчик #аналитик`. В публичном API все распознанные значения доступны
в `grades`, `profiles`, `countries` и `regions`; ими пользуются фильтры каталога.

В режиме `shadow: true` бот записывает фактическое решение и причины в событие
`publication_audience_decision` и продолжает текущую доставку. Сайт сохраняет
публичную выборку. Для проверки перед переключением передайте канонические
`JobRecord` по одному JSON-объекту на строку через stdin.

```shell
python -m scripts.eval.preview_publication_audience --tenant-config job_ftch/adapters/telegram_bot/config/tenants/ai_jobs.yaml --target site < canonical-jobs.jsonl
```

Результат содержит ID, ссылку, теги, версию правила и причины по каждому условию.
Сводные счётчики пишутся в stderr. `shadow: false` включает отбор с теми же
правилами. При недостатке данных редактор может выбрать `review` либо `skip`.
Изменения правил требуют перезагрузки конфигурации тенанта. Квитанции Telegram
сохраняются и при смене правил.
