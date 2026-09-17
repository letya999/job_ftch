---
title: "P1 — Полный ingest и ключевые парсеры"
description: "Спецификация P1: требования и проверяемая приёмка."
updated: 2026-09-12
---
# P1 — Полный ingest и ключевые парсеры

**Decisions**: [ADR 095](../adr/095-complete-ingest-parsers.md)
**Plan**: [Implementation plan](../plans/recovery-p1-complete-ingest-parsers.md)
**Initiative**: [Recovery execution](../plans/production-recovery-execution.md)

## Goal

Устранить потерю detail content и полей в common ingest и известных job boards.

## Scope

- rich_payload достаточен только при действительном full content; title/ID/snippet требуют detail.
- Переиспользовать shared API/JSON-LD/DOM extraction с field provenance; не добавлять host switches в core.
- GeekJob получает detail; GetMatch не пропускает detail из-за API-sniffer fallback; HireHi сохраняет organisation/location/workmode/baseSalary evidence без доверия заглушкам.
- Проверить Hirify API/429, Habr detail/deadlines, HH parser и discovery/pagination всех ключевых boards; bypass implementation принадлежит P3.
- Не выдавать CSS/JS/Next Flight/browser check/общую доску за vacancy text.
- Partial detail/source outcomes явны и retryable; проверить existing snapshot protection и content-version replay.
- Названия HRFi/HRK/GitJob/Jobseeker/Gcic сопоставить с точными source URLs; непонятные aliases включить в unresolved matrix.

## Runtime model

Tenant остаётся operational scope. Изменения backward-compatible и additive; SanitizeNode первый, type-changing paths через Stage, terminal decision принадлежит EvidenceDecisionNode. Полный source text относится к observations, секреты и профили не попадают в logs/fixtures/report.

## Metrics and observability

Результаты проверки связываются с run/source/observation/group/delivery и версией реализации. Ошибка, partial, missing, conflict, controlled drop и physical delivery различимы. Порогов live recall/precision без baseline не придумывать.

## Acceptance criteria

1. GetMatch 36153/36124 full detail и GeekJob short-listing/full-detail regression fixtures.
2. HireHi JSON-LD сохранение и conflicting placeholder fixtures.
3. Next Flight/CSS/WAF body rejects; pagination/partial failure/revisit changed same URL tests.

## Compatibility and safety

Работа только в своём worktree. Production reads разрешены лишь bounded/read-only; внешние writes, платные пробы, deploy, DB migrations/cleanup/replay, restart сервисов запрещены до отдельного подтверждения. Не коммитить/пушить без явной команды. Не читать raw secret files и не выводить DSN/пароли/ключи. Не запускать recipe reset на production.

## Technical appendix

Ownership: infrastructure/sources/career_site_source.py, site_parsers/, monitors/, scrapers/ и собственные tests/fixtures. Не редактировать bypass/, nodes/, tenant_runner или shared config.
Зависимости: Независимый старт; existing metadata compatibility. Сведение в S3/S5.
При необходимости изменить чужой ownership передать предложение integration owner, не редактировать чужие общие файлы параллельно.

## Open questions

Live provider quota, exact source aliases и требуемые production credentials остаются неизвестными до фактического получения. Исторические удалённые сообщения и прежние stage traces восстанавливаются только при наличии первичного архива. Если implementation требует нового shared contract, сначала согласовать с integration owner.

