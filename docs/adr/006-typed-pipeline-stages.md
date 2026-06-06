# 006 - Typed Pipeline Stages

**Status**: ACCEPTED
**Date**: 2026-06-06

## Context
The pipeline previously assumed one item type from source through sink. That blocks the next phase where `RawItem` must become `Job`, and later `Job`-typed match/search stages must stay type-safe.

## Decision
Introduce a generic `Stage[In, Out]` protocol with `async process(item: In) -> Out | None`.
`SanitizingNode` and `ProcessingNode` remain same-type specializations for the current raw pipeline.
`Pipeline` now accepts typed stages and treats the sanitize step as the explicit boundary before later type-changing stages.

## Consequences
- (+) `RawItem -> Job` extraction has a first-class contract instead of implicit unions.
- (+) Future `Stage[Job, Job]` nodes for search/match fit the same orchestration model.
- (-) Pipeline internals become more generic and require stricter tests around stage composition.
