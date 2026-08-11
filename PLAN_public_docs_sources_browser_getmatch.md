---
title: "Plan: public docs, live source registry, browser control, Getmatch parser"
status: "draft-for-delegation"
updated: 2026-08-11
owner_note: "Рабочий план для делегирования другой ИИ. Это не публичный roadmap."
---
# Plan: public docs, live source registry, browser control, Getmatch parser

## 0. Задача

Нужно реализовать пять связанных направлений:

1. Публиковать пользовательскую open-source документацию через MkDocs на GitHub Pages из `docs/`, с исключением рабочих/внутренних папок.
2. На этом же сайте показать актуальный список источников, которые питают публикации в `https://t.me/ai_engineer_jobs`.
3. Завести простой публичный roadmap фич/проблем в Markdown.
4. Дать агенту/MCP возможность управлять browser/bypass workflow для поиска вакансий под резюме пользователя.
5. Сделать проработанный parser для Getmatch, потому что текущий generic path даёт проблемы.

Критичная поправка: источники для `ai_engineer_jobs` нельзя брать из fixtures и нельзя руками обновлять в репозитории. Оператор меняет источники через Telegram-бота, значит публичный список должен отражать runtime state из БД/runtime store tenant `ai_jobs`.

## 1. Контекст репозитория

Перед работой прочитать:

- `AGENTS.md`;
- `docs/vision.md`;
- `docs/architecture.md`;
- `docs/ontology/compiler.md`;
- `docs/recipes/pipeline_recipe.md`;
- `docs/tech_stack.md`;
- `docs/rules.md`;
- `docs/operations/ci-cd.md`;
- browser/bypass docs в `docs/sources/`;
- relevant ADR про source snapshots, runtime overlays, browser/bypass boundaries.

Существующее состояние, которое нужно учитывать:

- MkDocs config уже есть в `docs_scripts/mkdocs.yml`;
- `docs_dir` уже указывает на `../docs`;
- docs requirements уже лежат в `docs_scripts/requirements.txt`;
- CI docs check уже есть, но deploy GitHub Pages надо отделить от PR check;
- tenant `ai_jobs` в production recipe описан как live tenant, но fixtures не являются источником истины для runtime source list;
- Telegram-бот уже умеет менять runtime sources;
- MCP уже имеет runtime-oriented tools, но browser/bypass capability workflow ещё не оформлен как высокоуровневый пользовательский сценарий;
- Getmatch сейчас не должен считаться решённым, если он проходит только через generic career ingest.

## 2. Нельзя делать

- Не генерировать публичный список источников из `fixtures/sources/ai_jobs.json`.
- Не заставлять оператора менять Markdown/fixtures/PR после изменения источников через Telegram-бота.
- Не публиковать приватные tenant/user IDs, cookies, tokens, proxy endpoints, auth headers, browser profiles, debug HTML или traces.
- Не обещать "обход любых антиботов". Browser/bypass layer должен быть управляемым, ограниченным, наблюдаемым и юридически честным.
- Не добавлять новый тяжёлый framework без предварительного обновления `docs/tech_stack.md` и проверки правил репы.
- Не делать hardcoded dispatch по host в core/config. Если нужен parser, он должен идти через существующий self-registration/site parser path.

## 3. Целевая архитектура

Публичный сайт на GitHub Pages состоит из:

- обычной документации из `docs/`;
- простой Markdown-страницы roadmap;
- страницы "Источники канала", которая читает public-safe source registry;
- machine-readable export `sources.json` или public read-only endpoint для той же информации.

Runtime source registry:

- source of truth: БД/runtime store tenant `ai_jobs`;
- reader: application/runtime API, тот же merged source catalog, который используют bot/API/MCP;
- sanitizer: allowlist public-safe полей;
- publisher: public endpoint или scheduled export;
- fallback: stale public-safe snapshot только если он явно помечен timestamp/status, но не fixtures.

Browser/resume workflow:

- пользователь даёт резюме или выбирает сохранённый profile;
- система создаёт search session;
- route planner показывает source routes и budgets;
- пользователь подтверждает sensitive routes;
- runner собирает кандидатов;
- pipeline фильтрует по существующей ontology/profile/evidence логике;
- пользователь получает вакансии, rejected/degraded summary и diagnostics.

## 4. Этап A: GitHub Pages для MkDocs

Цель: сделать автоматическую публикацию docs site.

Работы:

- проверить текущий `.github/workflows/docs.yml`;
- оставить PR workflow как `mkdocs build --strict` без deploy;
- добавить workflow для deploy Pages на push в `main`;
- использовать official GitHub Pages actions: configure pages, upload artifact, deploy pages;
- permissions: `contents: read`, `pages: write`, `id-token: write`;
- environment: `github-pages`;
- concurrency: один Pages deploy одновременно;
- сборка должна идти через `uv` и `docs_scripts/requirements.txt`;
- не переносить `mkdocs.yml` в root, если существующий `docs_scripts/mkdocs.yml` уже принят в проекте.

Проверки:

- `uv run --no-sync mkdocs build -f docs_scripts/mkdocs.yml --strict`;
- CI docs job;
- Pages artifact не содержит `.env`, `.runtime`, caches, profiles, debug bundles.

Acceptance:

- PR проверяет docs, но не публикует;
- push в `main` публикует GitHub Pages;
- сайт открывает пользовательские docs;
- internal/generated directories исключены через `exclude_docs` или структуру nav.

## 5. Этап B: public-safe source registry из БД

Цель: публичный список источников `ai_engineer_jobs` обновляется после изменений через Telegram-бота без изменения репозитория.

Найти текущий runtime source path:

- `TenantRunner.list_sources`;
- Telegram bot handlers для add/disable/clear sources;
- FastAPI endpoints `/pipeline/sources/{tenant_id}`;
- MCP tools для source list/upsert/enable;
- storage schema/runtime store для tenant sources, snapshots и source health.

Спроектировать public contract:

```python
class PublicSourceRegistryEntry:
    source_id: str
    kind: str
    public_name: str | None
    public_url: str | None
    public_handle: str | None
    enabled: bool
    status: Literal["enabled", "disabled", "degraded", "candidate"]
    category: str | None
    region: str | None
    last_success_at: datetime | None
    last_checked_at: datetime | None
    public_failure_reason: str | None
    parser_route_summary: str | None
```

Allowlist fields only:

- source id/slug safe for public display;
- kind;
- public URL/handle only if source itself is public;
- enabled/status/degraded state;
- health timestamps;
- category/region;
- short public-safe reason.

Denylist fields:

- credentials, tokens, cookies, headers;
- proxy config, auth providers, browser profile paths;
- private Telegram entities;
- internal tenant/user IDs except public tenant slug;
- resume/profile/shot data;
- raw HTML, traces, logs, screenshots;
- private notes and debug metadata.

Implementation options:

1. Preferred: read-only public endpoint.
   - Add runtime/app method `list_public_sources(tenant_id)`;
   - expose endpoint like `/public/tenants/{tenant_id}/sources.json`;
   - protect with tenant allowlist: initially only `ai_jobs`;
   - cache response with short TTL;
   - public docs page fetches JSON client-side or links to endpoint.

2. Fallback: scheduled production export.
   - Job reads DB/runtime store;
   - writes sanitized `sources.json`;
   - publishes to Pages artifact/static storage;
   - includes `generated_at`, `tenant_slug`, `source_count`, `stale` flag;
   - no repo commit on every source change.

Tests:

- sanitizer removes secret-bearing fields;
- public registry uses runtime/DB source list, not fixtures;
- add/disable source through runner changes public registry output;
- missing DB/API returns explicit error/stale state, not fixture data;
- private Telegram source is hidden or redacted.

Acceptance:

- changing source list through Telegram bot is reflected publicly without PR;
- public registry has timestamp and source count;
- no private fields leak;
- fixtures remain tests only.

## 6. Этап C: public source docs page

Цель: добавить пользовательскую страницу на GitHub Pages, которая объясняет источники канала.

Работы:

- добавить `docs/sources/public_registry.md` или аналогичную страницу;
- объяснить, что список строится из runtime tenant config;
- показать таблицу/виджет данных из public JSON;
- для no-JS fallback дать ссылку на `sources.json`;
- добавить поля: type, name, URL/handle, status, last success/check, category/region;
- добавить блок privacy: какие поля не публикуются;
- добавить ссылку на Telegram channel.

Acceptance:

- docs build проходит strict;
- страница не содержит захардкоженного списка текущих источников;
- если JSON недоступен, пользователь видит понятное сообщение.

## 7. Этап D: простой публичный roadmap в Markdown

Цель: сделать именно public roadmap, не task tracker.

Работы:

- создать `docs/roadmap.md` только на этапе реализации roadmap, не в этом plan-only изменении;
- структура: Now, Next, Later, Known problems, Non-goals, Recently shipped;
- порядок текущих направлений:
  1. GitHub Pages;
  2. live source registry from DB/runtime;
  3. Getmatch parser;
  4. source health diagnostics;
  5. browser capability inventory;
  6. resume-driven search session;
  7. human-in-the-loop login/challenge handling;
  8. parser coverage/live regression checks.

Правила:

- Markdown only;
- без генератора;
- не обновлять при каждом изменении источников;
- не дублировать `docs/techdebt.md`.

Acceptance:

- roadmap есть в docs nav;
- текст публично безопасен;
- roadmap не содержит internal IDs/secrets/runtime private notes.

## 8. Этап E: Getmatch parser

Цель: сделать Getmatch предсказуемым source/parser, а не generic best effort.

Investigation:

- определить, есть ли у Getmatch structured endpoint/API/embedded JSON/JSON-LD;
- собрать fixtures:
  - listing/search page;
  - detail page;
  - empty result;
  - changed layout;
  - challenge/authwall/degraded state;
- проверить текущий generic career ingest behavior и где он теряет вакансии.

Implementation:

- добавить parser в `job_ftch/infrastructure/sources/site_parsers/`;
- parser должен self-register по существующему паттерну;
- fetcher остаётся thin: получает artifact, но не извлекает vacancy business fields;
- parser извлекает candidates/drafts;
- canonical URL/dedup normalizer должен стабильно работать;
- source health должен различать:
  - `empty_result`;
  - `layout_changed`;
  - `challenge_required`;
  - `auth_wall`;
  - `parser_error`;
  - `deadline`.

Tests:

- normal listing returns expected vacancies;
- detail enrichment extracts title/company/location/work mode/salary/apply URL when present;
- empty state is not parser failure;
- changed layout is degraded/failure, not successful zero-yield;
- challenge/authwall emits public-safe diagnostics;
- dedup/canonical URL stable.

Acceptance:

- Getmatch можно добавить через runtime source flow;
- parser regression catches known current issue;
- failed Getmatch source is explainable in source health;
- no site-specific switch is added to core.

## 9. Этап F: browser/bypass capability inventory

Цель: agent/MCP видит, какие browser routes доступны, и может планировать сбор без магии.

Capability groups:

- direct HTTP;
- stealth HTTP/TLS route;
- browser route;
- persistent session route;
- proxy-backed route;
- manual challenge route;
- disabled/unavailable route with reason.

Работы:

- найти текущий bypass route graph и browser lifecycle implementation;
- добавить typed capability model на adapter/runtime boundary;
- expose inventory через MCP/API;
- добавить per-capability:
  - id;
  - availability;
  - cost/risk;
  - required secrets/provider state;
  - supports JS;
  - supports session;
  - supports proxy;
  - hard timeout;
  - max concurrency;
  - public-safe description.

Guardrails:

- запрет arbitrary executable path/browser profile path from user request;
- no cookies/tokens/proxy details in logs/public artifacts;
- explicit approval for headed browser, persistent profile, proxy, login/manual challenge;
- bounded retries and deadlines;
- guaranteed teardown before broadening browser workflows.

Acceptance:

- MCP/API can list capabilities;
- route planner can explain why route selected or unavailable;
- sensitive details are redacted;
- tests cover unavailable/missing-secret capability states.

## 10. Этап G: resume-driven search session

Цель: high-level workflow "вот резюме, проверь источники и найди релевантные вакансии".

MVP flow:

1. `ingest_resume` or reuse existing candidate profile/shot ingestion.
2. `create_search_session(tenant_id, user_id/profile_id, source_scope)`.
3. `plan_source_routes(session_id)`.
4. User approval for budgets/sensitive routes.
5. `run_search_session(session_id)`.
6. `get_search_session_status(session_id)`.
7. `list_search_results(session_id)`.
8. `explain_rejected_or_degraded(session_id, source_id/job_id)`.
9. `cancel_search_session(session_id)`.

Data to persist:

- session id;
- tenant id;
- profile id/user id with privacy boundaries;
- selected sources;
- route plan;
- approvals;
- budgets/deadlines;
- run ids;
- result job ids/groups;
- rejected/degraded summary;
- provenance.

Reuse existing pipeline:

- do not create separate relevance logic;
- use existing ontology/profile/evidence decision path;
- source runners remain source runners;
- session layer orchestrates and explains.

Acceptance:

- user can run workflow without knowing low-level pipeline commands;
- every source has status: checked, skipped, failed, degraded, needs_manual, no_results;
- results link back to source/run/decision evidence;
- session cancellation stops browser tasks safely.

## 11. Observability and release gates

Add metrics/events:

- public registry generated/read count;
- source registry stale/error;
- public sanitizer rejection count;
- Getmatch yield/failure/degraded reason;
- browser route chosen/fallback;
- browser hard timeout/teardown;
- search session created/running/completed/cancelled/failed;
- cost/budget counters.

Release checks:

- docs build strict;
- architecture boundary check if code touched;
- focused unit tests for registry sanitizer/Getmatch/capability planner;
- integration test for runtime source add/disable -> public registry output;
- security scan before commit;
- no public artifact contains private fields.

## 12. Suggested PR sequence

PR 1: docs Pages deploy.

- Add deploy workflow;
- adjust docs excludes/nav only if needed;
- verify strict build.

PR 2: public-safe live source registry.

- Runtime method from DB/store;
- sanitizer;
- tests;
- public endpoint or export job.

PR 3: public source docs page.

- Page that consumes/link public JSON;
- privacy explanation;
- docs nav.

PR 4: Getmatch parser.

- Fixtures;
- parser;
- health diagnostics;
- regression tests.

PR 5: browser capability inventory.

- Typed capability model;
- MCP/API exposure;
- route planner diagnostics;
- safety tests.

PR 6: resume-driven search session.

- Session model;
- MCP/API workflow;
- result/rejected/degraded explanation;
- cancellation and timeout checks.

## 13. Delegation instructions for next AI

Start with PR 1 unless the user explicitly chooses another PR.

Before coding:

- read the files listed in section 1;
- run `git status --short`;
- preserve unrelated changes and `.codex-backups/`;
- run `uv run ai-repo-safety doctor --agent-plan` if available;
- state which PR slice is being implemented.

During coding:

- keep changes scoped to one PR slice;
- prefer existing runtime APIs and self-registration patterns;
- never read `.env` or secret-bearing files;
- avoid adding dependencies;
- update docs/tests alongside behavior.

Before handoff:

- run the smallest relevant tests;
- for docs changes run `uv run --no-sync mkdocs build -f docs_scripts/mkdocs.yml --strict`;
- for code changes run relevant unit tests and architecture checks;
- run `uv run ai-repo-safety scan --target .` before commit;
- report changed files, checks run, and remaining risks.

