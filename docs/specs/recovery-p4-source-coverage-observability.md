---
title: "P4 — Контроль источников, пропусков и роста хранения"
description: "Спецификация P4: требования и проверяемая приёмка."
updated: 2026-09-12
---
# P4 — Контроль источников, пропусков и роста хранения

**Decisions**: [ADR 098](../adr/098-source-coverage-observability.md)
**Plan**: [Implementation plan](../plans/recovery-p4-source-coverage-observability.md)
**Initiative**: [Recovery execution](../plans/production-recovery-execution.md)

## Goal

Собрать воспроизводимую baseline matrix всех sources, независимую выборку пропусков и размеры хранилищ; подготовить metrics contract.

## Scope

- Переснять актуальный source registry; отдельные enabled/degraded/reliable/detail-complete/field-correct/profile-contributing признаки.
- Canonicalize aliases/keyword expansions; repeated observations не считать unique jobs.
- Исторический интервал ограничить реально доступной историей; установить last complete/nonempty success и причины regression.
- Read-only SSH/Postgres/OpenObserve inventory с statement timeout и default_transaction_read_only; никакого production ingest/run/replay.
- Измерить DB tables/indexes/TOAST/WAL, OpenObserve volume и Docker logs раздельно; не предполагать причину роста.
- Сопоставить bounded independent board inventory и observations; пропуски классифицировать по этапу, не утверждать точный recall при sample.
- Передать unique event IDs, counter definitions, conservation checks и missing-available denominator; runtime instrumentation делает S5.

## Runtime model

Tenant остаётся operational scope. Изменения backward-compatible и additive; SanitizeNode первый, type-changing paths через Stage, terminal decision принадлежит EvidenceDecisionNode. Полный source text относится к observations, секреты и профили не попадают в logs/fixtures/report.

## Metrics and observability

Результаты проверки связываются с run/source/observation/group/delivery и версией реализации. Ошибка, partial, missing, conflict, controlled drop и physical delivery различимы. Порогов live recall/precision без baseline не придумывать.

## Acceptance criteria

1. Offline scripts fixture tests для aliases, repeats, stage loss и event duplicate accounting.
2. Read-only scripts имеют bounded range/rows/time и не показывают credentials/PII.
3. Матрица содержит все enabled IDs или explicit unavailable access; size report и gaps привязаны ко времени.

## Compatibility and safety

Работа только в своём worktree. Production reads разрешены лишь bounded/read-only; внешние writes, платные пробы, deploy, DB migrations/cleanup/replay, restart сервисов запрещены до отдельного подтверждения. Не коммитить/пушить без явной команды. Не читать raw secret files и не выводить DSN/пароли/ключи. Не запускать recipe reset на production.

## Technical appendix

Ownership: scripts/audit/ новые read-only scripts, fixtures/tests собственного направления, reports и metrics proposal docs. Не редактировать tenant_runner, stores, source health runtime, dashboards или shared config.
Зависимости: Независимый старт; inventory нужен S4, metrics contract нужен S5.
При необходимости изменить чужой ownership передать предложение integration owner, не редактировать чужие общие файлы параллельно.

## Open questions

Live provider quota, exact source aliases и требуемые production credentials остаются неизвестными до фактического получения. Исторические удалённые сообщения и прежние stage traces восстанавливаются только при наличии первичного архива. Если implementation требует нового shared contract, сначала согласовать с integration owner.

