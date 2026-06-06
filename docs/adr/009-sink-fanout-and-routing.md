# 009 - Sink Fan-Out And Routing

**Status**: ACCEPTED
**Date**: 2026-06-06

## Context
The pipeline previously emitted to exactly one sink, which blocks simultaneous outputs and downstream routing such as main output, review queue, and quarantine/rejection channels.

## Decision
Support sink composition with `FanOutSink` and `RoutingSink`. `Pipeline` accepts either a single sink or a sequence of sinks; sequences are normalized into `FanOutSink`. Routing remains an explicit sink adapter rather than new orchestration branches inside the core pipeline.

## Consequences
- (+) Multiple outputs become possible without changing pipeline logic.
- (+) Conditional delivery stays outside the core orchestration loop.
- (-) Flush/finalize behavior must now be propagated across composite sinks.
