---
title: "Roadmap"
description: "Публичный roadmap job_ftch: текущий фокус, следующие шаги, известные ограничения и недавно поставленное."
updated: 2026-08-11
---
# Roadmap

Короткий **публичный** roadmap продукта. Это не issue tracker, не реестр
технического долга и не операционный runbook.

Актуальный список источников канала
[t.me/ai_engineer_jobs](https://t.me/ai_engineer_jobs) **не** ведётся на этой
странице: он строится из runtime/DB и публикуется отдельно. См.
[публичный реестр источников](sources/public_registry.md).

## Now

- Довести публичную документацию и live source registry до стабильного
  пользовательского контракта (страница + machine-readable JSON, без ручного
  списка в репозитории).
- Усилить диагностику здоровья источников: понятные public-safe статусы
  (`enabled` / `disabled` / `degraded` / `candidate`) и причины сбоев без
  утечки приватных деталей.
- Расширять coverage site-парсеров там, где generic career path даёт
  нестабильный результат.

## Next

- Human-in-the-loop сценарии для login / challenge: явные шаги подтверждения,
  без обещания «обойти любой антибот».
- Live regression checks по парсерам: ловить layout change, empty result и
  challenge/auth wall как объяснимые состояния, а не «тихий ноль».
- Улучшать resume-driven search session: маршруты источников, бюджеты,
  rejected/degraded summary для пользователя.

## Later

- Более широкий inventory browser/HTTP маршрутов с cost/risk и явным
  approval для чувствительных режимов.
- Дополнительные career-site parsers через self-registration, без hardcode
  host-switch в core.
- Публичные метрики покрытия и качества ingest (агрегированные, без PII и
  секретов).

## Known problems

- На части карьерных сайтов generic extract всё ещё хрупкий: смена вёрстки
  или SPA-листинг может дать пустой или неполный yield.
- Защищённые сайты требуют ограниченных browser/challenge маршрутов;
  доступность зависит от runtime capabilities и конфигурации оператора.
- Публичный реестр источников зависит от доступности runtime API; GitHub
  Pages сам по себе не читает БД.
- Список источников канала меняется оператором (бот/runtime) и **не**
  синхронизируется коммитами в этот roadmap.

## Non-goals

- Универсальный crawler «всего интернета».
- Обещание обхода любых anti-bot / CAPTCHA / login wall.
- Hardcoded список текущих источников канала в Markdown или fixtures как
  source of truth.
- Публикация секретов, cookies, proxy endpoints, browser profiles, private
  tenant/user IDs, resume/profile данных или raw debug traces.
- Автоматический issue-tracker, generator roadmap или sync с GitHub Issues
  для этой страницы.
- Тяжёлые orchestrator/queue frameworks (Kafka, Celery, Airflow и т.п.) как
  обязательный runtime.

## Recently shipped

- Публикация пользовательской документации через MkDocs / GitHub Pages.
- Public-safe live source registry (runtime/DB → sanitizer → JSON endpoint и
  docs page), без fixture-driven public list.
- Специализированный Getmatch site parser и связанные regression fixes.
- Browser capability inventory для agent/MCP (какие маршруты доступны и
  почему).
- Resume-driven search session: высокоуровневый workflow поиска вакансий
  под профиль/резюме поверх существующего pipeline.

---

Внутренний рабочий реестр отложенных инженерных задач — в
[techdebt](techdebt.md). Архитектурные решения — в [ADR](adr/index.md).
