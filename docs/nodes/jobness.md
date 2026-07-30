---
title: "Jobness nodes"
description: "Raw и post-extraction IS_JOB evidence из post_type distribution."
updated: 2026-07-27
---
# Jobness nodes

`jobness.py` содержит узлы, которые превращают post type distribution в typed
`IS_JOB` evidence. Это не самостоятельный relevance gate.

## RawJobnessEvidenceNode

**Вход/выход:** `RawItem -> RawItem`.

Читает `metadata.post_type_distribution` или fallback
`preclassified_post_type`/`preclassified_confidence`, строит
`JobnessDecision` diagnostic и добавляет `EvidenceAtom` по claim `IS_JOB`.

Если dominant type — `candidate_seeking`, `announcement` или `spam`, atom
получает `CONTRADICTS`; если `job_posting` с probability >= 0.5 —
`SUPPORTS`.

## JobnessEvidenceProducer

**Вход/выход:** `JobRecord -> JobRecord`.

Post-extraction вариант, который использует preserved post type distribution и
добавляет `jobness_post_extraction` evidence atom в metadata.

## JobnessDecisionNode

Compatibility alias для `RawJobnessEvidenceNode`. Новые graphs должны
использовать явный raw или post-extraction contract.

## Границы

Jobness отвечает только на вопрос “похоже ли это на вакансию”. Profile match,
risk, freshness и hard constraints решаются другими evidence claims.
