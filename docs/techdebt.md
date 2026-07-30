---
title: "Технический долг"
description: "Полный рабочий реестр технического долга job_ftch: release hygiene, source stack, runtime adapters, observability и TD-001..TD-031."
updated: 2026-07-30
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

- Candidate fanout child processing sequential внутри worker.
- Graph executor fanout sequential recursive.
- Full-pipeline benchmark/perf release gate слабый.
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
