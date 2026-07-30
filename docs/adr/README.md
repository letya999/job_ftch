---
title: "Architecture Decision Records"
description: "This directory contains ADRs for `job_ftch`."
updated: 2026-07-24
---
# Architecture Decision Records

This directory contains ADRs for `job_ftch`.

## Immutable history

> Note: ADR number 021 is used by two files (career-site monitor/scraper split and
> module boundary enforcement). Do not reuse 021 for new ADRs; the next free number is 078.

Do not rewrite existing ADR files.

- If context changed, add a new ADR and reference the newer one.
- If an newer ADR is obsolete, supersede it with a new ADR instead of editing it.
- Historical ADRs may remain `PROPOSED` even after code evolved; treat them as history first.

## Format

```markdown
# NNN — Title

**Status**: PROPOSED | ACCEPTED | DEPRECATED | SUPERSEDED-BY-NNN
**Date**: YYYY-MM-DD

## Context
## Decision
## Consequences
```

## Index

### 001-010

1. [001-hexagonal-architecture.md](001-hexagonal-architecture.md) — Hexagonal architecture
2. [002-ddd-lite.md](002-ddd-lite.md) — DDD Lite
3. [003-input-quarantine-flow.md](003-input-quarantine-flow.md) — Input quarantine flow
4. [004-pipeline-node-contracts-and-stats.md](004-pipeline-node-contracts-and-stats.md) — Pipeline node contracts and stats
5. [005-raw-item-identity-and-dedup.md](005-raw-item-identity-and-dedup.md) — Raw item identity and dedup
6. [006-typed-pipeline-stages.md](006-typed-pipeline-stages.md) — Typed pipeline stages
7. [007-extension-registry-and-plugin-discovery.md](007-extension-registry-and-plugin-discovery.md) — Extension registry and plugin discovery
8. [008-declarative-career-site-extraction.md](008-declarative-career-site-extraction.md) — Declarative career-site extraction
9. [009-sink-fanout-and-routing.md](009-sink-fanout-and-routing.md) — Sink fan-out and routing
10. [010-reliability-and-recovery-policies.md](010-reliability-and-recovery-policies.md) — Reliability and recovery policies

### 011-020

11. [011-source-spec-auth-provider.md](011-source-spec-auth-provider.md) — SourceSpec + AuthProvider separation
12. [012-store-connector-protocol.md](012-store-connector-protocol.md) — StoreConnector protocol hierarchy
13. [013-filter-profile-configurable-relevance.md](013-filter-profile-configurable-relevance.md) — Configurable relevance profile
14. [014-search-embedding-vector-protocol-stack.md](014-search-embedding-vector-protocol-stack.md) — Search / embedding / vector stack
15. [015-ingestion-mode-bypass-strategy.md](015-ingestion-mode-bypass-strategy.md) — Ingest modes and bypass strategies
16. [016-job-group-cross-source-aggregation.md](016-job-group-cross-source-aggregation.md) — Cross-source job grouping
17. [017-notification-sink-event-broadcasting.md](017-notification-sink-event-broadcasting.md) — Notification sink event broadcasting
18. [018-job-catalog-and-search-architecture.md](018-job-catalog-and-search-architecture.md) — Job catalog and persistent search
19. [019-embeddings-and-vector-storage-boundary.md](019-embeddings-and-vector-storage-boundary.md) — Embeddings and vector storage boundary
20. [020-registry-fallback-named-backend.md](020-registry-fallback-named-backend.md) — Registry fallback named backend

### 021-030

21. [021-career-site-monitor-scraper-split.md](021-career-site-monitor-scraper-split.md) — Career-site monitor / scraper split
21b. [021-module-boundary-enforcement.md](021-module-boundary-enforcement.md) — Module boundary enforcement
22. [022-cloakbrowser-advanced-bypass.md](022-cloakbrowser-advanced-bypass.md) — CloakBrowser advanced bypass
23. [023-adaptive-scraping-escalation.md](023-adaptive-scraping-escalation.md) — Adaptive scraping escalation
24. [024-canonical-job-contract-and-matching-funnel.md](024-canonical-job-contract-and-matching-funnel.md) — Canonical job contract and matching funnel
25. [025-adaptive-site-intelligence.md](025-adaptive-site-intelligence.md) — Adaptive site intelligence
26. [026-runtime-source-overlay.md](026-runtime-source-overlay.md) — Runtime source overlay
27. [027-runtime-candidate-profile-overlay.md](027-runtime-candidate-profile-overlay.md) — Runtime candidate profile overlay
28. [028-nlp-retrieval-quality.md](028-nlp-retrieval-quality.md) — NLP retrieval quality
29. [029-llm-extraction-points.md](029-llm-extraction-points.md) — Three explicit LLM touchpoints
30. [030-ontology-store.md](030-ontology-store.md) — Ontology store

### 031-040

31. [031-run-based-source-snapshot.md](031-run-based-source-snapshot.md) — Run-based source snapshot
32. [032-classification-eval-harness.md](032-classification-eval-harness.md) — Classification eval harness
33. [033-plugin-based-domain-parsers.md](033-plugin-based-domain-parsers.md) — Plugin-based domain parsers
34. [034-store-backend-auto-fallback.md](034-store-backend-auto-fallback.md) — Store backend auto fallback
35. [035-secretstr-policy-for-settings.md](035-secretstr-policy-for-settings.md) — `SecretStr` policy for settings
36. [036-unified-snapshot-filter-single-tenant.md](036-unified-snapshot-filter-single-tenant.md) — Unified snapshot filter in single-tenant path
37. [037-adaptive-scraping-escalation-policy.md](037-adaptive-scraping-escalation-policy.md) — Adaptive scraping escalation policy
38. [038-native-transformers-reranker.md](038-native-transformers-reranker.md) — Native transformers reranker
39. [039-composition-root-placement.md](039-composition-root-placement.md) — Composition root placement
40. [040-secondary-composition-root.md](040-secondary-composition-root.md) — Secondary composition root

### 041-046

41. [041-three-layer-relevance-pipeline.md](041-three-layer-relevance-pipeline.md) — Three-layer relevance pipeline
42. [042-binary-relevance-routing.md](042-binary-relevance-routing.md) — Binary relevance routing
43. [043-langfuse-observability.md](043-langfuse-observability.md) — Langfuse observability
44. [044-parallel-scoring-node.md](044-parallel-scoring-node.md) — Parallel scoring node
45. [045-managed-shot-backend-boundary.md](045-managed-shot-backend-boundary.md) — Managed shot backend boundary
46. [046-source-assessment-adapter.md](046-source-assessment-adapter.md) — Source assessment adapter

### 047-050

47. [047-adaptive-pipeline-item-concurrency.md](047-adaptive-pipeline-item-concurrency.md) — Adaptive pipeline item concurrency
48. [048-proxy-tier-in-adaptive-bypass-chain.md](048-proxy-tier-in-adaptive-bypass-chain.md) — Proxy tier in adaptive bypass chain
49. [049-library-first-deployment-image-boundaries.md](049-library-first-deployment-image-boundaries.md) — Library-first deployment image boundaries
50. [050-browser-session-bypass-protocol.md](050-browser-session-bypass-protocol.md) — Browser session bypass protocol

### 051-060

51. [051-production-equivalent-evaluation-and-graph-contract.md](051-production-equivalent-evaluation-and-graph-contract.md) — Production-equivalent evaluation and graph contract
52. [052-immutable-observation-ledger-and-content-versioned-replay.md](052-immutable-observation-ledger-and-content-versioned-replay.md) — Immutable observation ledger and content-versioned replay
53. [053-durable-outbox-and-delivery-idempotency.md](053-durable-outbox-and-delivery-idempotency.md) — Durable outbox and delivery idempotency
54. [054-terminal-deferred-and-retryable-pipeline-states.md](054-terminal-deferred-and-retryable-pipeline-states.md) — Terminal, deferred and retryable pipeline states
55. [055-one-to-many-candidate-segmentation-contract.md](055-one-to-many-candidate-segmentation-contract.md) — One-to-many candidate segmentation contract
56. [056-structured-evidence-jobness-and-extraction.md](056-structured-evidence-jobness-and-extraction.md) — Structured evidence, jobness, and extraction
57. [057-hybrid-retrieval-and-cross-encoder-reranking.md](057-hybrid-retrieval-and-cross-encoder-reranking.md) — Hybrid retrieval and cross-encoder reranking boundary
58. [058-calibrated-multi-axis-decision-policy.md](058-calibrated-multi-axis-decision-policy.md) — Calibrated multi-axis decision policy and single DecisionNode
59. [059-provisional-identity-and-canonical-group-commit.md](059-provisional-identity-and-canonical-group-commit.md) — Provisional identity index and canonical JobGroup commit
60. [060-versioned-ontology-lifecycle-and-llm-suggestions.md](060-versioned-ontology-lifecycle-and-llm-suggestions.md) — Versioned ontology lifecycle and LLM suggestion approval
61. [061-source-family-and-observation-kind.md](061-source-family-and-observation-kind.md) — Source family, observation kind and acquisition transport
62. [062-unified-evidence-and-confidence.md](062-unified-evidence-and-confidence.md) — Unified evidence and confidence aggregation
63. [063-controlled-evidence-fanout-and-deferred-resolution.md](063-controlled-evidence-fanout-and-deferred-resolution.md) — Controlled evidence fan-out and deferred resolution
64. [064-post-accept-enrichment-queue.md](064-post-accept-enrichment-queue.md) — Post-accept enrichment queue
65. [065-compact-responsibility-evidence-classification.md](065-compact-responsibility-evidence-classification.md) — Compact responsibility evidence classification
66. [066-heuristic-triage-before-post-accept-extraction.md](066-heuristic-triage-before-post-accept-extraction.md) — Heuristic triage before post-accept extraction
67. [067-compact-judge-jobness-evidence.md](067-compact-judge-jobness-evidence.md) — Preserve jobness evidence from the compact relevance judge
68. [068-promotion-eval-label-and-split-contract.md](068-promotion-eval-label-and-split-contract.md) — Promotion evaluation label and split contract
69. [069-split-operational-and-ml-observability.md](069-split-operational-and-ml-observability.md) — Split operational and ML observability
70. [070-mvp-run-delivery-and-graph-promotion-contract.md](070-mvp-run-delivery-and-graph-promotion-contract.md) — MVP run, delivery, and graph promotion contract

### 071-080

71. [071-durable-delivery-and-runtime-degradation.md](071-durable-delivery-and-runtime-degradation.md) — Durable delivery and runtime degradation
72. [072-career-site-deadline-and-global-work-budgets.md](072-career-site-deadline-and-global-work-budgets.md) — Career-site deadline and global work budgets
73. [073-nodriver-agpl-license-risk.md](073-nodriver-agpl-license-risk.md) — Nodriver AGPL-3.0 deployment obligations
74. [074-adaptive-route-state-graph.md](074-adaptive-route-state-graph.md) — Adaptive route-state graph and single execution context
