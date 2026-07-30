---
title: "TfidfLogregRelevancePrefilterNode"
description: "Trainable negative-only relevance prefilter на TF-IDF + logistic regression."
updated: 2026-07-29
---
# TfidfLogregRelevancePrefilterNode

`TfidfLogregRelevancePrefilterNode` — trainable prefilter, который доказывает
только отрицательный сигнал: score ниже порога означает “скорее нерелевантно”.
Узел никогда не принимает вакансию окончательно; accept остаётся за
decision/evidence layer.

## Вход и выход

**Вход:** `RawItem`.

**Выход:** `RawItem` с `relevance_prefilter_score`, если score >= threshold или
режим shadow.

**Drop:** в `gate` mode возвращает `None`, если score < threshold.

В degraded mode всегда pass-through и пишет
`relevance_prefilter_degradation`.

## Модель и параметры

`model_path` по умолчанию:
`fixtures/prefilter/tfidf_logreg_v1.json`.

`threshold = 0.30` by constructor default. The production graph can override
this; the current `ai_jobs` MVP recipe uses `0.20` to trade some precision
headroom for recall while preserving the negative-only hard gate. The node is
still a cost-saving hard gate: scores below the configured threshold do not
reach the LLM relevance judge.

`mode = gate` или `shadow`.

Model artifact содержит schema version, vocabulary, idf, coefficients,
intercept, ngram range, sublinear tf и training metadata. Если файл отсутствует,
не читается или schema version не совпадает, узел деградирует в pass-through.

## Логика inference

Текст токенизируется regex’ом, строятся n-grams, считается TF-IDF vector,
нормализуется и прогоняется через logistic regression sigmoid. Score пишется в
OpenTelemetry span и metadata.

В `shadow` mode низкий score не дропает item, а добавляет отрицательный
`EvidenceAtom` по `PROFILE_RELEVANCE`. В `gate` mode низкий score возвращает
`None`.

## Observability

`stats` хранит degraded status, reason, passed/dropped/degraded passthrough
counters. `preflight_status()` возвращает structured информацию для runtime
preflight.

## Границы

Нельзя трактовать высокий score как accept. Это только экономия LLM вызовов и
negative evidence; любые positive решения должны оставаться downstream.
