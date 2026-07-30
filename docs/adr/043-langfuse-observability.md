---
title: "ADR-043: Langfuse-based evaluation and observability"
description: "Status: ACCEPTED"
updated: 2026-07-24
---
# ADR-043: Langfuse-based evaluation and observability

Status: ACCEPTED
Date: 2026-06-22

## Context

Need to track LLM costs, relevance quality, and pipeline behavior across runs.
Needed both tracing (per-item spans) and eval datasets (gold labels).

## Decision

Langfuse as the primary observability layer:
- M1: OTel tracing via opentelemetry-exporter-otlp-proto-http. Per-node exit_stage
  spans sent to Langfuse OTLP endpoint. Enabled via JOB_FTCH_OTLP_ENDPOINT setting.
- M2: Langfuse datasets for gold eval (456-item gold_eval.jsonl, managed via langfuse SDK).
- M3: Experiment runner computes precision/recall against gold dataset per pipeline config.

Langfuse SDK installed via [langfuse] extras group (not [eval]).

## Consequences

Full pipeline observability with cost tracking per LLM node. Eval dataset decoupled
from codebase (no fixtures/dataset/ files committed).

Operational source-ingestion logs and metrics were later assigned to OpenObserve by
ADR-069. Langfuse remains the ML/LLM/RAG tracing and evaluation surface.
