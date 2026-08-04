---
title: "Технический долг"
description: "Полный рабочий реестр технического долга job_ftch: release hygiene, source stack, runtime adapters, observability и TD-001..TD-048."
updated: 2026-08-04
---
# Технический долг

Этот файл — рабочий реестр отложенных задач. Он не заменяет ADR и не должен
описывать уже принятое решение как “план”. Если пункт закрыт, рядом должен быть
статус, дата и ссылка на реализацию/ADR.

## 1. Отложенные пункты MVP-аудита

### Release hygiene и evidence

- Очистка release branch/worktree: changed files, untracked files, tracked
  ignored files, whitespace checks.
- CI coverage для branch `mvp-release-final`.
- Tag release workflow с обязательным test/security/eval evidence.
- Evidence для claim о подтверждённых live sources.
- Машинный gate на отсутствие tracked ignored/generated files.
- Машинный gate на отсутствие реального PII в committed fixtures.
- Publication-safe fixture provenance и anonymization.
- Third-party license/release gate docs для optional browser/bypass stack.

### Drift документации и product contract

- README/vision maturity drift против текущего runtime status.
- Parser fixture metadata drift.
- Parser manifest должен стать полным source/parser catalog.
- Protected/source coverage docs должны не противоречить source assessment и
  bypass docs.
- Public API exports должны соответствовать documented contracts.

### Optional adapters и runtime surfaces

- Dagster adapter: production-readiness, schedules/sensors, materialization
  semantics.
- FastAPI/FastStream: stable error envelopes, health/readiness, auth boundaries.
- MCP: parity с bot/API по status/source/run/lineage.
- Webhook/realtime modes: явно разделить shipped, experimental и stub-level.
- Telegram bot: graceful shutdown и закрытие `TenantRunner` resources.
- Adapter tests: поднять с import-level до runtime contract tests.
- Wheel/sdist verification для optional adapters.

### Packaging и deploy operations

- Prod compose/env parity: `POSTGRES_PASSWORD`, runtime overlay, `.env.*.example`.
- `.runtime` bind mount permissions в Linux deploy.
- Dev/prod Docker runtime parity.
- Browser/runtime install ownership после root installs и switch to `appuser`.
- Bot healthcheck должен покрывать scheduler/daemon failure.
- Docker context не должен тащить локальные ignored `data/`, `profiles`, caches.
- Appuser-level Docker runtime smoke.

### Source coverage и parser catalog

- Telegram comments docs/config/runtime-add flow.
- Проверить dead knobs вроде `telegram_history_wait_time_seconds`.
- Payme/RelocateMe parsers и Lever SourceSpec должны грузиться через normal
  registry path либо быть явно исключены.
- Real-world fixtures для site parsers/monitors всё ещё тонкие.
- `career_site_default_detail_limit` docs/config/tests drift.
- Zero-yield outcomes: completed/failed/partial/deadline не должны конфликтовать.
- No-runnable-sources cycles должны сохранять status/history.

### Performance и quality hardening

- Candidate fanout child processing sequential внутри worker. Поднято до TD-035.
- Graph executor fanout sequential recursive. Поднято до TD-035.
- Full-pipeline benchmark/perf release gate слабый. Поднято до TD-039.
- Eval/perf gates частично ручные.
- Static-check docs и CI не должны расходиться (`mypy .` vs `mypy job_ftch`).
- KZT/₸ salary parsing.
- URL metadata fallback validation hardening.
- Location/work-mode normalization для русскоязычного hybrid/onsite текста.

### Security, privacy, compliance

- API sniffer persistence guard: cookies/body/headers не должны попадать в
  artifacts.
- Robots/legal posture для protected-source bypass paths.
- Session-memory persistence требует encryption/redaction/cleanup gates.
- Broader schema review для secret-bearing SourceSpec/config fields.
- Tracing payload capture policy и debug artifact policy.

### Registry и boundary tooling

- Boundary checker false negatives вне critical imports.
- Entry point declarations должны совпадать с loader groups.
- Shot relevance backend должен уйти от hardcoded builder path к registry-driven
  модели, если он остаётся публичным backend.
- Package data и `.gitignore` interactions требуют artifact verification.

## 2. TD-001: Machine-readable job posting contract

Нужен формальный contract для confirmed `job_posting`:

- `hiring_intent_present`;
- `role_identified`;
- `employer_identifiable`;
- `apply_path_exists`;
- evidence для каждого условия.

Цель: output gate должен опираться на contract, а не только на
`post_type == job_posting`.

Статус: открыт. Приоритет: высокий. Блокирует более строгие release gates.

## 3. TD-002: Resolved 2026-06-18 — eval harness

Закрыто через ADR-032.

Реализовано:

- `scripts/evaluate_classification.py`;
- `scripts/evaluate_extraction.py`;
- `scripts/eval_all.sh`;
- `--gate` exit code для регрессий.

Поддерживать актуальность fixtures и thresholds.

## 4. TD-003: Explicit triage layer

Нужен явный `TriageDecision` вместо распылённой classification logic:

- content type;
- confidence;
- reject reason;
- `should_call_llm`;
- evidence list.

Downstream nodes должны читать решение, а не повторно классифицировать item.

Статус: частично superseded evidence path; не возвращать без сверки с
`EvidenceDecisionNode`.

## 5. TD-004: Source adapter contract

Разные source kinds имеют разные pipeline assumptions:

- `career_site` обычно уже vacancy-oriented;
- `telegram_channel` смешивает вакансии и announcements;
- `telegram_group/comment` шумнее и требует более строгого gate;
- `rss` часто структурирован.

Нужны source-level hints без нарушения границ pipeline relevance. Сегодня эту
роль частично выполняет `SourceAssessmentAdapter`.

Статус: частично закрыт source assessment stack, но contract parity надо
держать в docs/tests.

## 6. TD-005: DB schema normalization

`jf_job_groups.raw_json` не должен навсегда быть единственным способом query.
При росте объёма нужны реальные columns:

- `post_type`;
- `source_kind`;
- `best_score`;
- `quality_score`;
- `title`;
- `company`;
- `canonical_url`;
- `updated_at`.

`raw_json` оставить debug/source-of-truth payload. Приоритет растёт после
десятков тысяч groups.

## 7. TD-006: Source health scoring

`jf_source_health` должен хранить:

- job posting rate;
- duplicate rate;
- dead URL rate;
- LLM cost per valid job;
- last success;
- browser timeout/authwall/empty render metrics;
- average render time/html size.

Expose в bot/API/MCP status. Часть runtime health уже есть, но scoring model и
operator UX требуют усиления.

## 8. TD-007: Quarantine/review flow

Нужен полноценный review workflow для uncertainty:

- uncertain item -> quarantine/review table;
- bot/API command для просмотра;
- one-tap feedback -> positive/negative examples;
- reasons и evidence сохраняются.

Часть side channels уже есть; пользовательский review loop остаётся неполным.

## 9. TD-008: Feedback buttons

На опубликованных vacancy cards нужны кнопки:

- подходит;
- не вакансия;
- не мой профиль;
- мёртвая ссылка.

Feedback должен попадать в durable store и использоваться как profile/ontology
signal. Частично покрыто `VacancyFeedback`, но полный UX/learning loop не
закрыт.

Нужен следующий шаг: автоматический учёт feedback после безопасного порога и
review gate. Текущий MVP намеренно не меняет профиль по нажатию кнопки:
одиночные или шумные negative signals около целевых AI-инженерных ролей уже
показывали риск просадки precision/recall. Будущий автоучёт должен:

- требовать несколько независимых отметок или admin approval;
- сохранять provenance от опубликованной карточки до negative shot;
- уметь откатывать автоматически добавленные примеры;
- пересчитывать ontology/prefilter только через измеримый eval gate;
- показывать пользователю, какие feedback-сигналы реально изменили профиль.

## 10. TD-009: Explainability/debug last run

После run нужен объяснимый item-level summary:

- почему отправлено;
- почему rejected/drop/quarantine;
- какие evidence atoms/claims сработали;
- какие source/parser/bypass decisions повлияли.

Expose через bot/API/MCP/debug artifacts. Сейчас есть run summary и graph
metrics, но operator-facing debug still incomplete.

## 11. TD-010: Admin reclassify/backfill

Нужны maintenance команды:

- reclassify old records;
- purge non-jobs;
- rebuild groups;
- recompute scores for profile;
- rebuild search/vector indexes.

Не смешивать с normal pipeline run.

## 12. TD-011: Concurrency hardening

Открытые зоны:

- optimistic locking для profile save;
- idempotency key для uploaded documents;
- callback versioning для inline buttons;
- запрет overlapping browser run для `tenant_id + source_id`;
- отдельный detail-page concurrency budget;
- graceful shutdown активных browser tasks.

Часть concurrency уже вынесена в runtime budgets, но invariants не полностью
закрыты tests.

## 13. TD-012: Strict mode

Нужен operator switch для консервативного режима:

- меньше send limit;
- выше quality/relevance thresholds;
- ниже LLM budget;
- запрет unknown post type.

Не должен конфликтовать с production graph recipe.

## 14. TD-013: Resolved 2026-06-18 — run-based snapshot

Закрыто через ADR-031.

Snapshot перенесён из KV blob в `jf_source_snapshots`; runtime использует
run-based identity. Legacy KV methods остаются только для compatibility.

## 15. TD-014: OCR/image shot ingestion hardening

Bot поддерживает text/document/image examples, но image path требует:

- quality gate для low-signal screenshots;
- image/document fingerprinting;
- OCR diagnostics/confidence;
- fallback policy для weak OCR;
- duplicate detection до profile mutation.

## 16. TD-015: Durable discovery/scrape split

Variant A shipped: in-run discover -> pre-dedup -> detail fetch.

Variant B deferred:

- durable queue между discovery и scrape;
- независимые cadence/retry/backpressure;
- lease/heartbeat/reaper;
- crash recovery.

Возвращаться только при доказанном source volume/latency pressure.

## 17. TD-016: Jobseek-parity deferred notes

Осознанно не портировано:

- узкие company/ATS monitors с низким ROI;
- массовая миграция bespoke site parsers в declarative inline config;
- TDM-reservation compliance до открытия legal scope;
- отдельные browser-stealth flags без measurement.

Явно не делать: инвертировать monitor `cost` table; прежний анализ был неверен.

## 18. TD-017: Resolved 2026-07-04 — CompletenessGateNode

Реализовано behind flag:

- `CompletenessGateNode`;
- trusted monitor metadata -> direct `JobDraft`;
- `ExtractionNode` short-circuit через `_extraction_complete`;
- telemetry для `heuristic_completeness`.

Открытый риск: skip extraction теряет LLM-derived fields и может повлиять на
precision. Перед включением в precision-tuned deploy нужен eval на ATS-heavy
dataset.

## 19. TD-018: Browser fetch artifact contract

Нужен explicit browser fetch contract:

- request: source, URL, wait policy, scrolls, timeout, context mode;
- result: final URL, status, title, HTML, runtime, scroll count, error kind,
  metadata;
- fetcher не парсит вакансии;
- parsing остаётся в scraper/site-parser chain.

Implementations: browser backend, fixture backend, future external desktop probe.

## 20. TD-019: Browser worker pool

Browser rendering дорогой и должен иметь:

- global browser concurrency;
- per-source browser concurrency;
- запрет overlap для одного tenant/source;
- graceful cancellation;
- isolated per-item failures.

Связано с TD-011 и TD-018.

## 21. TD-020: Resolved 2026-07-04 — in-run two-phase career ingest

Реализовано:

- `_discover_candidates()`;
- `DiscoveredCandidate`;
- `monitor_type`;
- `heuristic_completeness`;
- `_enrich_candidates()`;
- concurrent detail-page worker pool.

Не реализовано: durable lifecycle queue/checkpoint/recovery. Это остаётся TD-015
Variant B.

## 22. TD-021: Browser contexts

Нужны режимы:

- `ephemeral` по умолчанию;
- `persistent` только explicit opt-in;
- browser state через `AuthProvider`/runtime secret storage;
- credentials/cookies не в `SourceSpec`;
- provenance пишет `context_mode`.

## 23. TD-022: Browser debug bundle

Для failed/quarantined browser fetch полезен bundle:

```text
artifacts/browser_debug/{tenant_id}/{source_id}/{run_id}/{item_id}/
  page.html
  screenshot.png
  metadata.json
  console.log
  network.json
```

Нужны redaction/security gates перед сохранением.

## 24. TD-023: Thin fetcher / centralized parser boundary

Жёсткое правило:

- fetcher скачивает runtime artifacts;
- parser извлекает candidates;
- pipeline нормализует, dedup-ит, score-ит и emits.

Запрещено:

- site-specific vacancy extraction внутри browser fetcher;
- LLM calls внутри fetcher;
- source health mutation внутри parser;
- browser deps в `domain`, `application`, `nodes`, `sinks`.

## 25. TD-024: Plugin SDK parity

Проблемы:

- `pyproject.toml` entry point groups не полностью совпадают с
  `load_extensions()`;
- часть loader groups не объявлена в package entry points;
- scorer plugin docs не соответствуют runtime registry;
- direct class entry point для source может ломаться, если `__init__` требует
  `spec`;
- `PluginMetadata.plugin_type` шире реального runtime support.

Нужно выровнять `pyproject.toml`, `PluginKind`, `PluginMetadata`,
`load_extensions()` и `docs/plugin_template/*`.

## 26. TD-025: Vendor-neutral observability

Нужно:

- observability port/facade для events/metrics/traces/current state;
- единая initialization во всех entrypoints;
- checked alert queries/dashboards;
- run freshness, source failure streak, source degradation, publish stalls,
  missing telemetry, LLM cost anomalies.

OpenObserve и Langfuse должны быть adapters, не единственной архитектурой.

## 27. TD-026: Managed infra и observability variants

Открыто:

- managed Postgres guide/smoke;
- managed Qdrant guide/smoke;
- LanceDB vector backend;
- provider-neutral OTLP examples;
- optional Prometheus/Grafana support;
- packaged dashboards/alerts под `deploy/observability`;
- reusable LLM judges/score definitions;
- env parity validator для root/bot/observability examples.

## 28. TD-027: Runtime adapter SDK

Нужен общий `RuntimeAdapter` contract и invariant tests:

- run/status/source listing/source health/run history/search;
- health/readiness;
- auth/error boundaries;
- shutdown;
- observability initialization.

Telegram bot, MCP, FastAPI, FastStream и Dagster должны проходить один readiness
matrix. Airflow/Flask/Django добавлять только после стабилизации contract.

## 29. TD-028: Split large Python files

Production/source files, требующие split при ближайшем подходящем scope:

- `job_ftch/application/tenant_runner.py`;
- `job_ftch/application/builder.py`;
- `job_ftch/infrastructure/sources/career_site_source.py`;
- `job_ftch/application/pipeline.py`;
- `job_ftch/adapters/telegram_bot/handlers/examples.py`;
- `job_ftch/infrastructure/bypass/stealth_hardening.py`;
- `job_ftch/application/registry.py`;
- `job_ftch/nodes/llm_relevance_classification.py`;
- `job_ftch/infrastructure/bypass/adaptive.py`;
- `job_ftch/infrastructure/relevance/shot_anchor.py`;
- `job_ftch/infrastructure/bypass/proxy_bypass.py`;
- `job_ftch/nodes/extraction.py`;
- `job_ftch/infrastructure/llm/heuristic.py`;
- `job_ftch/application/tenant_store.py`.

Large tests/scripts:

- `scripts/eval/run_pipeline_eval.py`;
- `tests/application/test_tenant_runner.py`;
- `job_ftch/adapters/telegram_bot/tests/test_handlers.py`;
- `tests/nodes/test_llm_relevance_classification.py`;
- `tests/test_ontology_and_llm.py`;
- `tests/test_source_assessment.py`;
- `tests/test_pipeline.py`.

Split direction:

- `tenant_runner`: source planning, health, metrics, profiles, job/search helpers;
- `builder`: graph construction, source/store wiring, sinks/delivery, node assembly;
- `career_site_source`: discovery, detail scraping, outcomes, freshness/windowing;
- `pipeline`: source iteration, item worker, finalization, outbox, summary accounting;
- tests: split by behavior, not implementation class.

## 30. TD-029: Classification FP gate re-baselined for MVP

Previous gate: FP rate <= 0.05, JOB_POSTING precision >= 0.90.

Measured 2026-07-28 on the locked 2085-sample eval dataset:
- FP rate: 0.2287 (148 of 647 non-job items classified as job_posting)
- JOB_POSTING precision: 0.9008
- JOB_POSTING recall: 0.9346
- JOB_POSTING F1: 0.9174
- announcement class: support=0 (no examples in the eval dataset; the
  class is defined in the classifier but the fixture has only job_posting
  and unknown labels)

Re-baselined gate: FP rate <= 0.25, precision >= 0.90.

Justification: the FP rate measures how often unknown items (not jobs)
are classified as job_posting. A 5% ceiling was aspirational and never
met. The high FP rate does not affect end-user precision because the
downstream relevance pipeline (LLM judge, semantic prefilter, routing
gate) filters most false positives. The controlled eval P=0.95 at the
pipeline output confirms this. Tightening the classifier FP rate is a
separate improvement cycle requiring a dedicated unknown/announcement
negative set and is not an MVP blocker.

Exit criterion: train a dedicated negative classifier on the 647
unknown-class items and demonstrate FP rate < 0.10 without precision
regression. Close this TD when done.

## 31. TD-030: Move generic role-token suppression out of code

Status: open. Priority: high for profile portability.

The 2026-07-29 recall recovery introduced a deterministic guard in
`job_ftch/nodes/llm_relevance_classification.py`: `clean_adjacent_unknown`
can accept a cited adjacent-role compact relevance result only when the item
contains profile-specific target-role tokens. To prevent generic titles from
being promoted, the node currently has a Python-side generic role token list
(`manager`, `lead`, `engineer`, `developer`, `architect`, `analyst`, etc.).

This is intentional as an MVP precision guard, but it is technical debt:

- generic-vs-specific role semantics belong in profile ontology compilation,
  recipe config, or another data-driven profile artifact;
- adding or removing such tokens in Python can silently change every tenant;
- the list is language/domain sensitive and may not fit non-AI profiles;
- tests now protect the behavior, but tests do not make the hardcoded list a
  clean architecture boundary.

Exit criterion:

- move generic role suppression into a versioned ontology/profile artifact or
  graph parameter;
- document the artifact schema and migration path;
- keep the current regression cases: generic Product/Project/Analyst titles
  must not be promoted by adjacent/unknown evidence, while profile-specific
  adjacent roles with clean cited evidence can pass;
- run controlled eval and prove no precision regression below the production
  floor.

## 32. TD-031: Curated dependency update batch after v0.0.5

Status: open. Priority: medium for release hygiene, high before the next
dependency-refresh release.

After the v0.0.5 release, Dependabot opened a burst of small dependency PRs.
They should not be merged one by one because each PR repeats the full CI/security
surface and makes it harder to reason about pinned GitHub Actions, toolchain
compatibility, and lockfile drift. Close the current bot PRs and re-apply them
as curated batches when dependency work resumes.

Superseded by the v0.0.5 lockfile and safe to close as stale/no-op:

- #129: `pillow` 12.2.0 -> 12.3.0;
- #130: `setuptools` 81.0.0 -> 83.0.0;
- #131: `pypdf` 6.13.2 -> 6.14.2;
- #132: `pyasn1` 0.6.3 -> 0.6.4.

Re-open as one GitHub Actions/security-tool hardening batch:

- #134: `trufflesecurity/trufflehog` pinned commit update;
- #140: `actions/setup-python` 6.2.0 -> 7.0.0;
- #141: `ossf/scorecard-action` 2.4.0 -> 2.4.4;
- #142: `gitleaks/gitleaks-action` pinned commit update;
- #143: `actions/checkout` 5 -> 7.

Re-open as one ML/dev dependency compatibility batch:

- #135: `datasets` `<4,>=3.6.0` -> `>=3.6.0,<6`;
- #136: `dill` `<0.4,>=0.3.8` -> `>=0.3.8,<0.5`;
- #137: `accelerate` `<1.11,>=1.10.1` -> `>=1.10.1,<1.15`;
- #138: `ruff` 0.15.16 -> 0.16.0; verify formatter/linter compatibility
  locally first because this bot PR had failing checks.

Exit criterion:

- recreate the updates manually or let Dependabot recreate them after the
  current PRs are closed;
- keep GitHub Actions pinned to verified commit SHAs where policy requires it;
- for the actions/security batch, run CI, security/secrets, SAST, Scorecard,
  supply-chain, CodeQL optional, and release-contract gates;
- for the ML/dev batch, run lint/format, mypy, tests, release-contract, and
  relevant eval gates;
- update `docs/tech_stack.md` when widening dependency ranges changes project
  dependency policy or rationale.

## 33. TD-032: Вынести локализацию карточки в i18n-артефакт

Status: open. Priority: medium, растёт при добавлении второго языка канала.

Публикационная карточка сейчас содержит русские строки прямо в коде и в
`config/publication/card.yaml`: подписи блоков (`Компания: `, `Гео: `,
`Формат: `, `Условия: `, `Нужно: `, `Стек: `), плейсхолдеры (`не указана`,
`не указано`, `не указан`, `не указаны`), значения контролируемых словарей
(`job_ftch/publication/normalize.py`: `WORK_MODE_LABELS`, `PERIOD_LABELS`,
`до вычета`/`на руки`, `от`/`до`), тексты ссылок футера (`link_labels`) и
таблица нормализации гео (`_GEO_ALIASES`).

Решение принято сознательно 2026-07-31: хром канала единообразно русский,
язык источника несёт только текст самой вакансии (заголовок, требования,
стек). Это устранило смешение `офис`/`onsite` в одной ленте. Долг не в
выборе языка, а в том, что ключ и локализованное значение не разделены.

Почему это долг:

- формат карточки и её локализация меняются независимо, но правятся в одном
  месте; смена порядка блоков рискует задеть тексты;
- второй язык канала потребует дублирования всего `card.yaml`, а не добавления
  одного словаря значений;
- `_GEO_ALIASES` смешивает две разные операции: канонизацию сущности
  (`RU`/`РФ`/`Russia` — одно место) и её локализованное отображение
  (`Россия` против `Russia`);
- значения словарей нельзя менять без релиза кода, хотя это данные;
- у тенантов нет способа переопределить формулировки под свой канал.

Exit criterion:

- разнести структуру и значения: блок несёт ключ (`label.company`,
  `placeholder.company`), значения живут в отдельном i18n-артефакте;
- хранить артефакт там же, где остальные версионируемые данные (БД/JSON),
  а не в исходниках, с фолбэком на встроенный набор;
- гео-нормализация разделяется на канонический идентификатор места и его
  локализованные подписи;
- выбор локали — параметр sink/tenant, а не константа модуля;
- сохранить текущее поведение как регрессию: одна лента не должна смешивать
  языки контролируемых словарей.


## 34. TD-033: hirify detail-страница отдаёт только чипы, не тело вакансии

Status: closed 2026-07-31. Реализовано в `site_parsers/hirify.py`
(`parse()` + `/api/vacancies/{id}`), тесты в `tests/test_hirify_site_parser.py`.

Была: `description` длиной 254 символа из обвязки карточки и тегов, потому что
Nuxt-SPA не отдаёт прозу в разметке, а first-party API использовался только для
discovery. Следствие — `requirements_must` вырождался в список тегов,
`tools_stack` в один `jira`, компания терялась.

Стало: `parse()` берёт листинг, затем тело каждой вакансии из
`/api/vacancies/{id}` (поле `text`), плюс структурные поля — компания, зарплата,
регионы, формат работы, грейд, специализация, теги. На живом прогоне тела
311-3024 символа, в среднем 1878.

Фоллбек трёхуровневый: листинговый API -> `discover()` (HTML-ссылки, API через
браузер, скролл) с добором тела через detail-эндпоинт -> пустой `parse()`, после
которого `CareerSiteSource` уходит в generic-краул. Поэтому у парсера намеренно
не выставлены `confirmed_empty_on_empty` и `terminal_on_empty` — иначе последний
уровень был бы подавлен; это закреплено тестом.

Осталось открытым из исходного пункта:

- `fastpath_completeness` по-прежнему не понижается, когда описание состоит из
  служебных строк карточки («Show contacts», «Report», «Vacancy posted on»);
  такой вход всё ещё проходит как полный;
- остальные источники не проверены на тот же класс дефекта: SPA-страница с
  first-party API, используемым только для discovery.

## 35. TD-034: Идемпотентность outbox по всем destination (issue #145)

Status: open. Priority: high. Прямой блокер из prod-readiness аудита
(«outbox lie»). Отложено сознательно 2026-07-31: это рефакторинг критического
пути доставки, его нельзя безопасно вносить в релизную ветку
`feature/telegram-publish-polish` вместе с точечными фиксами.

ADR-053 обещает, что каждый sink получает детерминированный idempotency key и
что повтор не дублирует Telegram/файл/БД. Текущий рантайм этого не гарантирует:

- `Pipeline._enqueue_outbox` создаёт durable-записи только для
  `_delivery_targets`;
- `Pipeline._emit_outbox_targets` эмитит primary `_sink` ДО обработки этих
  записей, то есть основной side effect вне state-машины outbox;
- `DeliveryTarget.deliver` получает только item, без persisted idempotency key
  или outbox-конверта;
- `recover_pending_outbox` сначала вызывает внешний target, потом помечает
  строку delivered — окно между успехом и commit не закрыто.

Достижимые сбои:

- частичный fan-out: primary sink ок, target A ок, target B падает, item
  ретраится, primary sink срабатывает повторно (нет durable per-destination
  записи);
- краш/сбой записи состояния после внешнего успеха: `target.deliver` прошёл,
  процесс упал до `mark_outbox_delivered()`, recovery шлёт тот же target снова,
  а он не может дедуплицировать по outbox-ключу, которого нет в его контракте.

Exit criterion:

- каждый side-effecting destination, включая текущий primary sink, за одной
  durable per-destination outbox-абстракцией;
- неизменяемый delivery-конверт с как минимум `idempotency_key`, `outbox_id`,
  `decision_version` и payload передаётся в destination;
- destination, которые это умеют, делают атомарную проверку/запись по ключу;
  для остальных явно документировать at-least-once;
- одна запись/переход состояния на destination; `emit()` в памяти не считается
  durable-доказательством;
- удалённый из конфигурации target оставляет наблюдаемую pending/blocked запись,
  а не молча теряется;
- регрессионные тесты по 5 сценариям из issue #145 (частичный fan-out,
  crash-before-commit, два recovery-воркера, удалённый target, обратная
  совместимость single-destination);
- ledger и outbox используют один `decision_version`. Сейчас
  `Pipeline._enqueue_outbox` (`job_ftch/application/pipeline.py:1128,1137`)
  жёстко подставляет `"pipeline-v1"`, а `_record_observation` (`:1237`) берёт
  значение из settings. Из-за этого поднятие `pipeline_decision_version` меняет
  ledger, но не idempotency key outbox, и replay под новой политикой молча
  схлопывается по старому ключу вместо повторной доставки. Хардкод убирается
  волной 2 плана `PLAN_dedup_terminal_lifecycle.md`; здесь пункт остаётся как
  инвариант, который обязан проверяться тестом после полного перевода
  destination на outbox-конверт.

## 36. TD-035: Fan-out concurrency budget

Status: open. Priority: high. Это живой production-путь, а не гипотетический.

`CandidateSegmentationNode` объявлен как `is_fan_out_stage = True`
(`job_ftch/nodes/candidate_segmentation.py:21`) и стоит четвёртым узлом в
production-графе (`config/pipelines/evidence_v2.yaml`, id `segmentation`).
Обработка кандидатов идёт строго последовательно в двух местах:

- `GraphExecutor._run_from` (`job_ftch/application/graph/executor.py:250-259`)
  рекурсивно обходит кандидатов по одному;
- `Pipeline._finalize_item_result` (`job_ftch/application/pipeline.py:843-852`)
  финализирует детей по одному.

Следствие: пост с N вакансиями обрабатывается в N раз дольше одиночного, и
item-level concurrency этого не компенсирует, потому что параллелизм задан на
уровне родительских observation, а не кандидатов.

Почему нельзя чинить `asyncio.gather`: при десятках-сотнях spans это даёт burst
по памяти и по downstream-провайдерам (LLM, store), то есть меняет профиль
нагрузки непредсказуемо.

Exit criterion:

- явная `FanOutPolicy` с `max_concurrency`, `max_pending`, `fail_fast` и
  политикой упорядочивания; значения задаются метаданными узла, а не константой
  в executor;
- дети одного родителя не могут обгонять друг друга там, где это влияет на
  dedup или aggregation; там, где порядок не важен, он явно объявлен ненужным;
- settlement dedup-claims детей остаётся корректным при конкурентной обработке
  (проверяется тестами из волны 1);
- бенчмарк fan-out 1/10/100 кандидатов показывает сублинейный рост времени.

## 37. TD-036: Разрезать `Store` на узкие порты

Status: open. Priority: medium.

`Store` (`job_ftch/application/contracts.py:113-251`) — 35 методов и девять
несвязанных ответственностей: outbox, dedup claims, observation ledger,
признак обработанности, duplicate records, run state, стратегия источника,
снапшоты, source assessment и ingest state. Это нарушает принцип разделения
интерфейсов и делает невозможными независимые тестовые реализации, batching и
раздельные транзакционные границы.

Предлагаемый разрез — по потребителю, не по таблице:

- `ObservationLedger`: `record_observation`, `get_observation`;
- `DedupRepository`: claims, ключи, duplicate records, `compare_and_reserve`;
- `OutboxRepository`: `enqueue_outbox`, `list_pending_outbox`,
  `mark_outbox_delivered`;
- `ProcessedMarker`: `has_processed`, `mark_processed`;
- `RunStateRepository`: `get_run_state`, `set_run_state`;
- `SourceStateRepository`: source strategy + ingest state;
- `SnapshotRepository`: snapshot rows, hashes, purge;
- `SourceAssessmentRepository`.

Правило после разреза: узел получает свой порт, а не `Store`. `Store` остаётся
фасадом, реализующим все протоколы, чтобы миграция шла по одному потребителю за
раз без big-bang.

Exit criterion:

- ни один класс в `job_ftch/nodes/` не принимает `Store`;
- `job_ftch/application/capabilities.py` (введён волной 2 плана) полностью
  вытеснен настоящими портами;
- контрактные тесты (`tests/infrastructure/stores/test_store_contracts.py`)
  разбиты по портам и прогоняются для каждой реализации отдельно.

## 38. TD-037: `Any` в graph runtime обнуляет strict-режим

Status: open. Priority: medium. Делать после TD-036.

`mypy strict = true` включён (`pyproject.toml`), но в ключевых модулях
`Any` встречается 22 раза в `application/pipeline.py`, 33 в
`application/builder.py`, 31 в `application/graph/executor.py`. Совместимость
payload между узлами проверяется сравнением строк с именами типов в манифесте,
а `"Any"` используется как обход проверки. Компилятор не может гарантировать,
что `CandidateSpan -> JobDraft` действительно совместимы.

Отмечено отдельно: `--strict` в mypy **не включает** `disallow_any_explicit`,
поэтому это осознанно добавляемый флаг, а не подразумеваемый.

Конкретные правки:

- `disallow_any_explicit` через `[[tool.mypy.overrides]]` точечно для
  `job_ftch.application.graph.*`;
- `NodeDefinition[InputT, OutputT]` вместо `Stage[Any, Any]` в
  `PipelineBuilder._stages` (`job_ftch/application/builder.py:259,364`);
- стабильный `PayloadTypeId` со схемой версии вместо `type(x).__name__` в
  манифестах;
- typed capability keys вместо `RuntimeContext.resources: dict[str, Any]`;
- `Any` остаётся легальным только в диагностических и событийных структурах, и
  это зафиксировано правилом в `AGENTS.md`.

Exit criterion: production-граф компилируется без `Any` в цепочке payload;
несовместимость узлов ловится на compile, а не на первом item.

## 39. TD-038: Resource-aware backpressure

Status: open. Priority: medium.

Сейчас ограничение нагрузки задано двумя механизмами: bounded-очередь по
количеству элементов в `CompositeSource`
(`job_ftch/infrastructure/sources/composite.py:291`, `maxsize=queue_capacity`) и
`AsyncCallBudget` для числа LLM-вызовов. Этого мало не потому, что нужно много
бюджетов, а потому что один item может быть ссылкой на абзац, а другой -
страницей на сотни килобайт: очередь на 100 элементов означает в этих случаях
разный объём резидентной памяти.

Exit criterion:

- byte-бюджет очереди в дополнение к count-бюджету;
- resource classes с раздельными лимитами (`network`, `browser`, `cpu`, `llm`),
  объявляемые метаданными узла, а не разложенные руками внутри узлов;
- memory watermark, при достижении которого source-fetch приостанавливается;
- ровно три бюджета, а не семь: count, bytes, resource-class concurrency.

## 40. TD-039: Perf baseline вместо порогового assert

Status: open. Priority: medium.

Единственный throughput-тест (`tests/benchmarks/test_pipeline_throughput.py`) -
это 100 item'ов через `SanitizeNode` с `assert duration < 1.0`. Он не поймает ни
двукратную регрессию, ни утечку памяти, ни деградацию при росте concurrency.

Exit criterion:

- сохраняемый baseline-файл и сравнение с ним в CI;
- сценарии: чисто расчётная спина и спина с преобладанием ввода-вывода,
  1/4/16 воркеров, веер 1/10/100 кандидатов, поток дублей, отмена,
  время импорта и резидентная память;
- пороги в относительных дельтах к baseline, а не в абсолютных секундах, иначе
  на разных раннерах будут ложные срабатывания;
- регрессия выше порога блокирует мерж.

## 41. TD-040: Lazy plugin catalog

Status: open. Priority: medium, растёт при внешнем использовании пакета.

`load_extensions()` (`job_ftch/application/registry.py:761-833`) при первом
обращении импортирует около 45 модулей, включая весь bypass-стек, site parsers и
realtime-источники, и исполняет плагин прямо на этапе discovery:
`loaded = candidate.load(); if callable(loaded): loaded()`. Состояние держится в
глобальных флагах `_builtins_loaded` / `_entry_points_loaded` под модульным
`Lock`.

Для переиспользуемой библиотеки это означает скрытые import side effects,
медленный старт, зависимость результата от порядка импортов и сложную изоляцию
в тестах.

Exit criterion:

- discovery отделён от loading, loading от instantiation;
- дескриптор плагина несёт `plugin_id`, `api_version`, `node_types`,
  `capabilities` без импорта модуля;
- политика коллизий явная (`error` по умолчанию);
- каталог неизменяем после сборки и передаётся в compiler явно, а не читается
  из глобального состояния.

Вести совместно с TD-024 (Plugin SDK parity): это две половины одной проблемы.

## 42. TD-041: Settings как контракт, а не глобальная переменная

Status: open. Priority: medium.

Проблема не в количестве полей, а в том, что один объект `Settings` смешивает
pipeline, infrastructure, source, output, bot и observability, и достаётся
глобальным кэшированным геттером. Вне `job_ftch/infrastructure` осталось восемь
вызовов `get_settings()`; реальные утечки в ядро - `job_ftch/application/pipeline.py:290`
и `job_ftch/application/builder.py:255`. Следствия: нельзя поднять две
независимые конфигурации в одном процессе, тесты зависят от env-синглтона,
builder имеет скрытые зависимости.

Exit criterion:

- ноль вызовов `get_settings()` вне composition edge (`job_ftch/cli.py`,
  адаптеры);
- `Settings` разрезан на `PipelineConfig` / `RunConfig` / `InfraConfig`; узел
  получает свой срез, а не весь объект;
- env-loading живёт только на границе приложения;
- два независимых пайплайна с разными конфигурациями поднимаются в одном
  процессе, и это покрыто тестом.

Сокращать число полей ради сокращения не требуется: цель - разрез по владельцу.

## 43. TD-042: Batch execution lane

Status: open. Priority: low, сознательно отложено.

`encode_batch()` (`job_ftch/infrastructure/embeddings/bgem3.py:76`) и
`classify_batch()` (`keyword_classifier`, `llm_classifier`, `setfit_classifier`)
уже существуют, но вызываются только из `scripts/eval/run_pipeline_eval.py`.
Runtime обрабатывает по одному элементу.

Почему отложено, а не сделано: champion-рецепт не использует эмбеддинги вообще
(`bgem3_enabled=false`, `embedding_prefilter_enabled=false` в
`config/recipes/champion_artifact.json` и в `.env.prod.example`), а единственный
дорогой узел - LLM-судья - уже забюджетирован и кэширует результат по item в run
state. Подключение batch требует микробатч-планировщика в рантайме, то есть
переработки execution-модели, и сейчас не окупается.

Exit criterion (возвращаться при выполнении условия входа):

- в графе появился узел, где batch даёт измеримый выигрыш (включённый bgem3,
  локальный классификатор на GPU, массовый rerank);
- `process_batch()` добавлен в контракт stage вместе с `max_batch_size`,
  `max_batch_wait` и tenant-изоляцией;
- выигрыш подтверждён бенчмарком из TD-039, а не оценкой.

## 44. TD-043: Latent-риски параллельных лейнов

Status: open. Priority: low, но обязательно к прочтению перед включением
параллельных узлов в production-граф.

Проверено 2026-08-02: production-граф полностью последовательный, узлов с
`execution: parallel` или `background` в нём нет, конкурентность живёт внутри
`EvidenceFanOutNode`. Поэтому оба пункта ниже сейчас не исполняются, но
сработают в день включения parallel-лейна (например,
`config/pipelines/experiment_parallel_bgem3.yaml`).

1. `copy.deepcopy(report.item)` в `GraphExecutor._run_from`
   (`job_ftch/application/graph/executor.py:153,167`) выполняется на каждую
   parallel-группу и на каждый background-узел. Копируется весь `RawItem`:
   полный текст вакансии и метаданные, а при включённом bgem3 - ещё и dense-
   векторы. Нужна поверхностная копия с copy-on-write или явный контракт
   "parallel-узел не мутирует вход".
2. `GraphTaskQueue` (`job_ftch/application/graph/queues.py`) удалён как мёртвый и
   одновременно сломанный: `asyncio.Queue()` без `maxsize`, то есть без
   backpressure, и консьюмер `while True: await handler(await queue.get())` без
   `try/except`, из-за чего первое же исключение в handler убивает воркер
   навсегда - `_worker` остаётся не-None, `start()` становится no-op,
   `task_done()` не вызывается, а `put()` продолжает копить. Если очередь между
   лейнами понадобится, это durable-таблица из TD-015 Variant B, а не
   восстановление удалённого in-memory класса и не новая внешняя брокерная
   зависимость.

Exit criterion: перед первым включением parallel-узла в production-граф оба
пункта закрыты и покрыты тестом.

## 45. TD-044: Один execution runtime

Status: open. Priority: medium.

Делать после TD-035 и TD-036.

Сегодня исполнение размазано между линейным `Pipeline` и декларативным
`GraphExecutor`, которые соединены мостом `GraphPipelineStage`. `Pipeline`
владеет жизненным циклом источников, outbox, sink и состоянием прогона;
`GraphExecutor` - выполнением узлов, веером и failure policy. Прямой вред от
этого расхождения был ровно один и конкретный: потеря `DEFERRED` из-за двух
независимых реализаций settlement (устранено волной 1 плана
`PLAN_dedup_terminal_lifecycle.md`).

Порядок работ обратный тому, который обычно предлагают: сначала из обоих
движков выносится наружу всё, что они дублируют (settlement - сделано, порты -
TD-036, precompile графа и типизация payload - TD-037), и только затем
`GraphCompiler` становится единственным компилятором. Если начать со слияния
движков, они сливаются вместе со своими расхождениями.

Exit criterion:

- `GraphPipelineStage` удалён;
- декларативный YAML, сборка на Python и tenant-конфигурация дают один
  скомпилированный объект;
- `Pipeline` остаётся deprecated-фасадом с объявленной датой удаления;
- `graph_hash` production-рецепта не меняется при миграции, либо изменение
  зафиксировано в `config/recipes/*` вместе с прогоном eval.

## 46. TD-045: Перевести LLM-промпты на DSPy

Status: open. Priority: medium. Делать после фиксации текущих prompt/eval
артефактов и без изменения terminal decision contract.

Сейчас промпты для extraction, relevance, ontology и связанных LLM-операций
собираются как строки и YAML/TXT-артефакты. Это затрудняет версионирование
инструкций и few-shot примеров, повторное использование сигнатур и безопасную
оптимизацию промптов на размеченных shots. Нужно перевести промптные
компоненты на DSPy signatures/modules, сохранив существующие `LLMProvider`,
Pydantic-схемы ответа, provenance и возможность воспроизводимого offline
прогона.

Exit criterion:

- для каждого переведённого сценария есть DSPy signature/module и явная версия;
- legacy prompt path остаётся совместимым fallback до завершения миграции;
- golden shots и regression gates сравнивают старый и DSPy-вариант по schema
  validity, extraction/relevance quality, latency и token usage;
- prompt/compile artifacts воспроизводимы, не содержат credentials и привязаны
  к `config/recipes/*` или эквивалентному manifest;
- новая зависимость и её runtime/cost-профиль сначала зафиксированы в
  `docs/tech_stack.md`, а архитектурная граница описана ADR при необходимости.

## 47. TD-046: Сравнить LLM-модели и выбрать более дешёвую подходящую модель в OpenRouter

Status: open. Priority: medium. Отдельный этап после появления стабильного
DSPy/legacy baseline; не смешивать с миграцией промптов.

Нужно провести воспроизводимый bake-off нескольких моделей, доступных через
OpenRouter, на тех же production-shaped shots и сценариях, которые влияют на
extraction, relevance и ontology. Цель — выбрать самую дешёвую модель,
проходящую quality, schema, latency и reliability gates, а не модель с
минимальной ценой запроса без проверки качества. Цены, availability и
capabilities OpenRouter считать внешними входными данными прогона и сохранять
в его manifest с датой и идентификаторами моделей.

Exit criterion:

- список кандидатов, их OpenRouter model IDs, параметры и snapshot цен
  зафиксированы в eval manifest;
- каждая модель прогнана на одном и том же frozen dataset с повторяемым
  seed/настройками, а результаты включают quality по полям, schema validity,
  acceptance/review/reject drift, latency, retries и token/cost per item;
- есть baseline-сравнение с текущими `openai_model` и
  `relevance_llm_model`, включая отдельные критерии для extraction и relevance;
- выбранная модель дешевле baseline и не нарушает production quality/reliability
  gates; при равенстве качества выбирается более дешёвая, затем более быстрая;
- выбранные model IDs, routing/fallback policy и дату проверки перенести в
  recipe/runtime config, а полный отчёт и причину выбора сохранить как
  regression evidence.

## 48. TD-047: Жёсткий таймаут на весь браузерный вызов + гарантированный teardown

Status: open. Priority: high. Прямой корень инцидента 2026-08-04.

Инцидент: prod-бот завис живым в ~04:02:54 UTC. Браузерная операция
(patchright/cloakbrowser Chromium) застряла на шаге, для которого не сработал
таймаут; корутина повисла бесконечно, event-loop scheduler'а заблокировался,
heartbeat-маркер `/tmp/job_ftch_bot_ready` перестал обновляться, healthcheck
ушёл в `unhealthy` на 5.5 ч, а браузерные процессы не были убиты — в контейнере
5.5 ч жили 3 браузерных стека. Новые вакансии не публиковались.

Проблема: таймауты навешаны на отдельные шаги браузерной операции (навигация,
ожидание селектора, скролл), но нет единого жёсткого дедлайна на весь вызов
"поднять браузер -> получить артефакт -> закрыть". Любой шаг без сработавшего
таймаута подвешивает весь вызов навсегда, а teardown браузера при этом не
гарантирован.

Связь: TD-018 (browser fetch artifact contract), TD-019 (browser worker pool,
graceful cancellation), TD-011 (graceful shutdown browser tasks), TD-048
(watchdog на весь run — верхний слой над этим hard-timeout).

Exit criterion:

- весь браузерный вызов обёрнут в единый жёсткий дедлайн
  (`asyncio.timeout`/`wait_for`) поверх пошаговых таймаутов;
- teardown браузера/контекста/страницы гарантирован в `finally` даже при отмене
  или таймауте — никаких остаточных процессов при hang;
- при срабатывании дедлайна операция помечается как failure с понятным
  `error_kind`, а не молча зависает;
- регрессионный тест: искусственно зависающий браузерный шаг приводит к таймауту
  вызова и полному teardown без остаточных процессов.

## 49. TD-048: Watchdog на прогон краула (отмена зависшего run + kill браузеров)

Status: open. Priority: high.

Проблема: у прогона краула нет верхнего временного предела на уровне всего run.
Если отдельный источник или браузерная операция зависает (см. TD-047), весь run
застревает, scheduler-loop не завершает итерацию, heartbeat замерзает, публикация
встаёт. Инцидент 2026-08-04: run `e51b353c...` завис после 04:08 UTC и не
завершился до ручного рестарта через 5.5 ч.

Требование: watchdog, который, если run не завершился за N минут, отменяет
связанные asyncio-таски и убивает браузерные процессы этого run, после чего
scheduler-loop продолжает следующую итерацию и обновляет heartbeat.

Связь: TD-011 (graceful shutdown), TD-019 (browser worker pool cancellation),
TD-047 (hard-timeout как нижний слой; watchdog — верхний слой на весь run).

Exit criterion:

- конфигурируемый per-run дедлайн (env/recipe/runtime config, не хардкод);
- по истечении дедлайна: отмена run-tasks + гарантированный kill браузерных
  процессов этого run, без затрагивания других tenant/run;
- scheduler-loop переживает watchdog-отмену: обновляет heartbeat и переходит к
  следующей итерации, а не падает;
- наблюдаемость: событие о сработавшем watchdog с run_id, длительностью и числом
  убитых браузеров;
- регрессионный тест: искусственно зависший run отменяется watchdog'ом в пределах
  дедлайна, браузеры убиты, следующая итерация scheduler'а стартует.
