---
title: "ADR-028: NLP Retrieval Quality — E5 Prefixes, Language Detection, Translation, Cross-Encoder Reranking"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# ADR-028: NLP Retrieval Quality — E5 Prefixes, Language Detection, Translation, Cross-Encoder Reranking

**Status**: ACCEPTED
**Date**: 2026-06-14

## Context

job_ftch scrapes job postings from 20 RU/KZ sources. Profiles are authored in Russian.
Four retrieval quality problems were identified in MVP planning:

1. **E5 prefix bug**: `multilingual-e5-small` requires `"query: "`/`"passage: "` prefixes per the E5 paper. Without them retrieval quality drops ~10-15%.
2. **Cross-lingual gap**: English-language postings score poorly against Russian profiles on BM25.
3. **No language awareness**: pipeline had no way to know what language a posting is in.
4. **Flat candidate ranking**: all candidates scored by cheap cosine only; no cross-lingual reranking pass.

## Decisions

### D1: E5 prefix fix (always-on, mandatory)

Add `embed_query(texts)` and `embed_passage(texts)` to `FastEmbedProvider`.
Call sites use duck-typing — `getattr(provider, "embed_passage", provider.embed)` — to stay
backward compatible with any `EmbeddingProvider` that doesn't implement the new methods.
The existing `embed()` method is preserved unchanged; the `EmbeddingProvider` Protocol is not modified.

### D2: Language detection (opt-in, `LANGUAGE_DETECTION_ENABLED`)

Use `lingua-language-detector` — best accuracy for short texts and handles Kazakh (`kk` ISO code).
Internal mapping: `kk → kz`. Slavic scripts (bg/uk/be/mk/sr) remapped to `ru` at confidence < 0.8
to avoid false positives on vocabulary-overlapping Cyrillic languages (pattern from support_rag project).

Architecture: `LanguageDetectionNode` in `nodes/` receives an injected `LanguageDetectorPort` (Protocol
in `contracts.py`). No external lib imports inside the node — only the Port. This preserves the
`nodes/` layer boundary: no external imports directly, only via Port injection.

Result stored in `job.metadata["detected_language"]` for downstream nodes to read.

### D3: Translation (opt-in, `TRANSLATION_ENABLED`)

Use `ctranslate2` + Helsinki-NLP `opus-mt-ru-en` / `opus-mt-en-ru` models for CPU-fast inference.
Supported pairs: RU↔EN only. KZ is not supported — `CTranslate2Translator.supports()` returns False
and `TranslationNode` skips silently, preserving original text. `multilingual-e5-small` handles KZ
cross-lingually without translation.

Pipeline position: AFTER `LanguageDetectionNode`, BEFORE `EmbeddingNode`. This way the embedding
is computed on the translated (normalized-to-Russian) text, improving BM25 matches too.

Originals preserved in `metadata["original_title"]` and `metadata["original_description"]`
for traceability and display.

### D4: Cross-encoder reranking (opt-in, `RERANKER_ENABLED`)

Model: `jinaai/jina-reranker-v2-base-multilingual` via fastembed `TextCrossEncoder`.
Supports 100+ languages including RU/EN/KZ. Runs in `asyncio.run_in_executor` to avoid blocking.

**Critical architecture decision**: reranking is NOT a pipeline node. Cross-encoders require a
query to score against — this doesn't exist at ingestion time. Reranking runs in the `/digest`
command handler (retrieval time) as a post-retrieval step:

1. Fetch `digest_size * N` candidates from store
2. Build profile query string from profile fields
3. Call `reranker.rerank(query, [doc texts])` → scored list
4. Sort by score, return top `digest_size`

`BufferSink[T]` was added to `sinks/` for future batch post-processing patterns that need all
items before taking action.

## Consequences

- All four features are independent; each can be enabled/disabled without affecting others
- E5 prefix fix has no config flag — it's always correct behavior
- Cold start cost: lingua ~500MB models at first detection call; opus-mt ~300MB per direction at first translation call
- CPU cost: reranker 200-500ms per `/digest` batch (acceptable for interactive UX; not for polling pipeline)
- KZ support: detected (lingua), vector-matched natively (multilingual-e5), not translated (no model)
- The `nodes/` layer boundary is preserved: all external lib access goes through injected Ports
