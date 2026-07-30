---
title: "CandidateSegmentationNode"
description: "Fan-out boundary RawItem -> CandidateSpan[] для digest и source-declared segments."
updated: 2026-07-27
---
# CandidateSegmentationNode

`CandidateSegmentationNode` — явная fan-out граница `RawItem ->
tuple[CandidateSpan, ...]`. Он разделяет одно raw observation на несколько
candidate spans, когда источник или текст явно содержит несколько вакансий.

## Вход и выход

**Вход:** `RawItem`.

**Выход:** tuple `CandidateSpan`; у каждого span есть
`parent_observation_id`, `ordinal`, `text`, ссылка на исходный `raw_item`,
`source_evidence` и `context_evidence`.

Если segmentation не доказана, возвращается один span со всем текстом.

## Логика

Source-confirmed `VACANCY_DETAIL` с `detail_vacancy_confirmed = True` считается
одной вакансией даже при нумерованных списках внутри описания.

Если metadata содержит `candidate_segments`, узел использует эти declared
segments как `source_declared_segment`.

Иначе текст режется по digest boundary перед numbered/bulleted блоками.
Разделение включается только если несколько частей несут vacancy hint
(`vacancy`, `hiring`, `ищем`, `вакансия`). Это защищает обычные bullets внутри
одной вакансии.

`parent_text`, `reply_chain_text`, `linked_message_text` переносятся в
`context_evidence`.

## Границы

Узел не классифицирует span как вакансию и не делает extraction. Он только
создаёт корректную единицу-кандидат для downstream nodes.
