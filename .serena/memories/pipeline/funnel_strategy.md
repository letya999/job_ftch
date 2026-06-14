# Funnel Strategy

- Target matching funnel is high-signal and multi-node, with cheap gates before expensive extraction.
- Intended node order:
  `SanitizeNode`
  `SourceContextNode`
  `PostTypeClassificationNode`
  `HardFilterNode`
  `DedupCandidateNode`
  `SemanticPrefilterNode`
  `ExtractionNode`
  `NormalizationNode`
  `AggregationNode`
  `MatchScoringNode`
  `RiskAndQualityNode`
  `RoutingNode`
- Interpret the flow as four logical rings:
  intake, understanding, canonicalization, decision and delivery.
- Important separation:
  post type answers "what is this";
  relevance answers "is it relevant for a profile";
  risk answers "is it suspicious";
  quality answers "is it complete and useful";
  aggregation confidence answers "how safely can it join a group".
- Keep the only hard raw-to-job type boundary at extraction time:
  `RawItem -> JobDraft`.
  Downstream nodes should operate on job-shaped data, not go back to raw text heuristics.
- Semantic prefilter must stay cheap.
  Its purpose is to avoid expensive extraction for obvious misses, not to replace final scoring.
- Dedup should be layered:
  exact seen check first, blocking keys second, fuzzy or semantic similarity only on candidates.
- Routing should be deterministic by policy and reason codes, not by implicit score magic.

## NLP Enhancement Nodes (opt-in, MVP batch B/C)

These nodes are inserted between AggregationNode and EmbeddingNode when enabled via config flags.

Full NLP pipeline order (all enabled):
  ... → AggregationNode → LanguageDetectionNode → TranslationNode → EmbeddingNode → MultiProfileMatchNode → ...

- `LanguageDetectionNode`: detects language of `title + description[:300]`, stores in `job.metadata["detected_language"]`.
  Uses injected `LanguageDetectorPort` — no external imports in the node. Enabled via `LANGUAGE_DETECTION_ENABLED=true`.
  LinguaLanguageDetector maps `kk→kz`, remaps Slavic langs to `ru` at confidence < 0.8.

- `TranslationNode`: translates title+description to target language (default: `ru`) when `detected_language` differs.
  Skips silently if detected_language == target, pair unsupported (KZ), or language unknown.
  Preserves originals in `metadata["original_title"]` and `metadata["original_description"]`.
  Enabled via `TRANSLATION_ENABLED=true` + `TRANSLATION_TARGET_LANGUAGE=ru`.

## Cross-Encoder Reranking (post-retrieval, NOT a pipeline node)

Cross-encoder reranking runs at retrieval time in `/digest`, not in the ingestion pipeline.
Reason: cross-encoders need a query to score against — this doesn't exist at ingestion time.

Flow in `/digest`:
1. Fetch `digest_size * 5` candidates from store
2. If reranker enabled: call `reranker.rerank(profile_query, docs)` → re-sort by score
3. Return top `digest_size` results

Model: `jinaai/jina-reranker-v2-base-multilingual` (278M params, 100+ langs, ~200-500ms CPU).
Enabled via `RERANKER_ENABLED=true`. `BufferSink[T]` available for future batch patterns.
