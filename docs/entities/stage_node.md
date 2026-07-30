---
title: "Stage / Node"
description: "**Слой**: `application` + `nodes/`"
updated: 2026-07-28
---
# Stage / Node

**Слой**: `application` + `nodes/`

## Что это

Узел пайплайна реализует `Stage[In, Out]`:

```python
async def process(self, item: In) -> Out | None
```

Node может:

- пропустить item дальше
- трансформировать тип
- вернуть `None` и дропнуть item

## Важные protocol variants

- `SanitizingNode` — обязательный первый stage
- `ProcessingNode[T]` — stage, который сохраняет тип
- `TypeChangingNode[In, Out]` — stage, который меняет тип payload

## Текущая логика пайплайна

В `job_ftch` runtime nodes образуют ordered chain, а не произвольный DAG.

Самые важные группы узлов:

1. Intake and freshness
   `SanitizeNode`, `SnapshotFilterNode`, `SourceContextNode`,
   `OntologySnapshotNode`, `CandidateSegmentationNode`

2. Early gates
   `GarbageFilterNode`, `PostTypeClassificationNode`, `HardFilterNode`,
   `DedupNode`

3. Cheap relevance and extraction prep
   embedding/BGE prefilter, `SemanticPrefilterNode`,
   `RawJobnessEvidenceNode`, `CompletenessGateNode`

4. Structured extraction
   `ExtractionNode`, `ExtractionValidationNode`

5. Canonicalization
   title/company/skills/location/compensation/lifecycle normalization

6. Evidence production
   `MultiProfileMatchNode`, `LexicalEvidenceNode`, `RiskScoringNode`,
   `QualityScoringNode`, `JobValidationNode`,
   `LLMRelevanceClassificationNode`

7. Terminal decision and post-accept path
   `EvidenceDecisionNode`, `JobAggregationNode`, durable delivery outbox,
   post-accept enrichment queue

## Практические правила

- `SanitizeNode` всегда первый
- `EvidenceDecisionNode` — единственная терминальная runtime-граница
- type changes делайте только через `Stage[In, Out]`
- дешёвые проверки ставьте раньше дорогих
- фильтрацию не нужно моделировать как exception, если это обычный controlled drop

## Что не делать

- не вставлять heavy LLM logic перед early filters
- не менять тип данных внутри `ProcessingNode[T]`
- не обходить builder invariants ручной сборкой, если нужен стандартный pipeline
- не возвращать legacy `RoutingNode` / `ParallelScoringNode` в основной runtime graph

## Связанные документы

- [Protocols](protocols.md)
- [PipelineBuilder](pipeline_builder.md)
- [RawItem](raw_item.md)
- [JobDraft](job_draft.md)
- [JobRecord](job_record.md)
- [Node catalog](../nodes/README.md)
- [PipelineBuilder, Pipeline и Graph](../pipelines/builder_and_graph.md)
