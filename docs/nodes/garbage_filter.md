---
title: "GarbageFilterNode"
description: "Ранний evidence-node для явно не-вакансий и мусорных career pages."
updated: 2026-07-27
---
# GarbageFilterNode

`GarbageFilterNode` добавляет отрицательное evidence по claim `IS_JOB`, если
raw text или career-site URL/заголовок похожи на навигационную, категорийную,
count/search или другую не-вакансионную страницу.

## Вход и выход

**Вход:** `RawItem`.

**Выход:** исходный `RawItem` без изменений, если garbage-сигнала нет; либо
`RawItem` с обновлённой metadata/evidence.

Узел не делает terminal drop. Найденный garbage остаётся evidence для позднего
decision layer.

## Что пишет

`garbage_evidence` — строковая причина.

`early_triage_state = uncertain`.

`evidence_atoms[]` — `EvidenceAtom` с `claim=IS_JOB`,
`polarity=CONTRADICTS`, producer `garbage_filter`, provenance `INFERRED`.

## Логика

Для `CAREER_SITE` отдельно проверяются типовые не-detail страницы: locations,
benefits, culture/company pages, listings, search/count endpoints и category
pages. Затем применяется общий `garbage_reason(text)`.

## Границы

Узел не кодирует профильную релевантность и не должен выбрасывать спорные
career-site страницы сам. Его задача — сделать негативный сигнал видимым для
evidence/decision stages, сохранив возможность rescue downstream.
