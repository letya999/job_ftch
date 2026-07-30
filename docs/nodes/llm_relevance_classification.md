---
title: "LLMRelevanceClassificationNode"
description: "LLM relevance judge для borderline/forced/disagreement cases с cache, budget и circuit breaker."
updated: 2026-07-27
---
# LLMRelevanceClassificationNode

`LLMRelevanceClassificationNode` обогащает `JobRecord` LLM relevance verdict’ом
в metadata `_llm_relevance` и добавляет typed LLM evidence atoms.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** `JobRecord` с обновлённым `relevance_score`, `_llm_relevance`,
optional evidence atoms и degradation metadata.

Узел может no-op, если item вне LLM call window/policy.

## Call policy

`threshold` mode вызывает LLM только между `low_threshold` и `high_threshold`;
если `parallel_final_score` есть, window считается по нему.

`force_all` вызывает LLM для всех surviving candidates.

`uncertainty_only` вызывает LLM только если `UncertaintyRouterNode` поставил
`needs_llm_review`.

## Prompt и cache

Profile выбирается по лучшему `profile_scores`, fallback — первый profile.
Prompt включает profile brief, positive/negative job examples и fenced vacancy
text. В compact mode используется evidence-oriented schema.

Cache key зависит от app revision, graph hash, thresholds, call policy,
classification mode, profile id, provider/model, response schema и prompt hash.
Это защищает от stale decisions после изменения graph/prompt/model.

## Budget, circuit breaker, fallback

`budget` или `max_per_run` ограничивают calls. При исчерпании budget узел пишет
`llm_relevance_degradation`.

Provider failure/circuit open превращается в degraded `_llm_relevance` с
decision `review`, а не в silent reject.

## Compact evidence mode

Compact result может пройти ambiguity resolution и precision confirmation,
если включены лимиты. Итоговый compact verdict превращается в
PROFILE_RELEVANCE/IS_JOB evidence atoms через `_append_llm_atom`.

## Границы

Узел производит LLM evidence, но не является final routing owner. Terminal
policy должна читать `_llm_relevance`/evidence downstream.
