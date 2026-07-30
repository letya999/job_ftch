---
title: "SourceClassifierNode"
description: "Опциональный classifier-gate для RawItem на уровне текста источника."
updated: 2026-07-27
---
# SourceClassifierNode

`SourceClassifierNode` — опциональный raw-level gate, который прогоняет текст
`RawItem` через внешний `ClassifierProvider` и отбрасывает элементы, уверенно
похожие на нецелевой контент источника. В default production pipeline узел не
подключён; он оставлен для кастомных конфигураций, экспериментов и тестов.

## Вход и выход

**Вход:** `RawItem` после sanitation/source context, с заполненным `text`.

**Выход:** тот же `RawItem` без изменений, если классификатор не дал
запрещённый label с достаточной уверенностью.

**Drop:** `RawItemDropped(reason=IRRELEVANT_CONTENT)`, если внешний classifier
вернул label из `candidate_seeking` или `spam` и `confidence >= confidence_threshold`.

Узел не создаёт `JobDraft`, не пишет evidence, не меняет metadata и не принимает
финальное решение о релевантности вакансии.

## Параметры

`classifier: ClassifierProvider` — dependency, у которого вызывается
`classify(item.text)`.

`confidence_threshold: float = 0.80` — минимальная уверенность для hard-drop.
Ниже порога item пропускается дальше, даже если label потенциально плохой.

## Логика работы

1. Берёт только `item.text`; source metadata, URL, tenant profile и ontology
   snapshot в решении не участвуют.
2. Вызывает внешний classifier.
3. Сравнивает `result.label` с локальным deny-list:
   `candidate_seeking`, `spam`.
4. Если label запрещён и confidence выше порога, бросает `RawItemDropped`.
5. В details кладёт label, confidence и `model_id`, чтобы drop был объясним в
   логах/метриках.
6. Во всех остальных случаях возвращает исходный `RawItem`.

## Границы ответственности

Это не `SourceAssessmentAdapter`. Source assessment отвечает за pre-ingest
capabilities источника: что источник умеет, какие обходы нужны, можно ли его
читать crawler/scraper/parser stack и какие ограничения есть до получения
конкретного поста.

Это не основной relevance gate. Production relevance строится вокруг evidence
pipeline (`lexical_evidence`, semantic/profile evidence, risk/quality,
`EvidenceFanOutNode`, `EvidenceDecisionNode`) и tenant/profile context.

`SourceClassifierNode` полезен только как ранний cheap guardrail для явно
плохих raw-posts, если есть отдельная модель классификации источникового шума.
Если classifier начинает решать “подходит ли вакансия пользователю”, его надо
выносить в evidence/relevance слой, а не расширять этот узел.

## Сайд-эффекты и состояние

Узел хранит только ссылку на classifier и порог уверенности. Между item’ами
состояние не накапливается. При drop исходный `RawItem` передаётся в исключение,
поэтому observability может восстановить текст и locator.
