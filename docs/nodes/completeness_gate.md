---
title: "CompletenessGateNode"
description: "Structured-source evidence и extraction-cost hint без решения jobness."
updated: 2026-07-27
---
# CompletenessGateNode

`CompletenessGateNode` извлекает доверенные structured metadata-поля из
`RawItem` и превращает их в evidence/cost hints. Он не утверждает, что item
точно является вакансией, и не принимает relevance decision.

## Вход и выход

**Вход:** `RawItem` с metadata от scraper/parser/monitor.

**Выход:** `RawItem` с `structured_source_evidence`, `evidence_atoms`,
`structured_page_kind`, `extraction_cost_hint`, `fastpath_completeness`.

## Completeness score

Score 0..1 складывается из title, достаточного текста, company,
canonical/job URL, location и salary/base salary. Порог по умолчанию `0.8`.

Trusted monitors и trusted extraction sources получают пониженный effective
threshold до `0.6`.

## Evidence

Для title/company/location/canonical_url/salary создаются
`StructuredSourceEvidence` и соответствующие `EvidenceAtom` с
`claim=FIELD_VALID`, `polarity=SUPPORTS`, producer `completeness_gate`.

## Extraction cost hint

`extraction_cost_hint = structured`, если источник/metadata достаточно
доверенные и complete; иначе `full`. Это подсказка для стоимости extraction, а
не разрешение пропустить validation/decision.

## Границы

Listing/category page может быть structured, но не быть vacancy detail.
Поэтому узел намеренно не пишет hiring intent и не превращает listing в job.
