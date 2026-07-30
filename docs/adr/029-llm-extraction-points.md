---
title: "019 — LLM Extraction Points: 3 Explicit Touchpoints"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 019 — LLM Extraction Points: 3 Explicit Touchpoints

**Status**: ACCEPTED
**Date**: 2026-06-17

## Context

The pipeline needs LLM capabilities for: ontology enrichment, borderline relevance classification, and presentable text formatting for Telegram. We must avoid making LLM a hot-path cost driver.

## Decision

**Three explicit LLM touchpoints**, each with its own cost control, caching, and metrics:

### 1. Live Ontology Enrichment (background, on shot upload)
- Trigger: `add_example_to_profile_with_enrichment()` or `load_resume_with_enrichment()`
- Input: text of positive/negative shot (resume or job)
- Output: `ShotExtraction` { skills, roles, seniority, anti_patterns }
- Update: writes to `OntologyStore` (DB or file)
- Cost: 1-5 calls per profile update. Negligible.

### 2. Low-Confidence Relevance Classification (runtime, gated)
- Trigger: `MultiProfileMatchNode` similarity ∈ (low_threshold, high_threshold)
- Input: job + profile (target_roles, required_skills, anti_preferences) + positive/negative job shots
- Output: `RelevanceClassification` { decision, confidence, reasoning, matched/mismatched }
- Update: writes to `Store.set_run_state("relevance:<key>", ...)`
- Cost control: `llm_relevance_max_per_run: int = 500`. Beyond that → fallback to threshold-based decision.
- Cache: stable across re-runs.

### 3. Presentable Text Formatting (runtime, before TG posting)
- Trigger: `PresentableTextNode` in pipeline, only for items going to `posting_telegram` sink
- Input: structured `JobRecord`
- Output: `PresentableJob` { title, body, salary_formatted, location_formatted, contact_section, tags, ats_score, language }
- Update: writes to `Store.set_run_state("presentable:<key>", ...)`
- Cost control: `llm_presentable_max_per_run: int = 50`. Beyond that → `_fallback` template format.
- Cache: stable across re-runs.

### NOT in LLM scope

- ❌ `ExtractionNode` (primary extraction) — heuristic + ontology. Speed and cost.
- ❌ `PostTypeClassificationNode` — embedding similarity. Only the borderline branch goes to LLM.
- ❌ `DedupNode` — hash checks. Always cheap.
- ❌ `SanitizeNode` — regex rules. Always cheap.

### LLM Configuration

- Default backend: `openai` (gpt-4o-mini)
- Mode: `instructor.Mode.TOOLS_STRICT` (OpenAI structured outputs API with constrained grammar)
- All system prompts in **English**
- Output rule: **respond in the language of the input text**
- Canonical names of skills/roles/technologies: always English lowercase regardless of input language

## Consequences

- (+) Predictable cost. LLM only on cold paths and explicit gates.
- (+) Universal parser. Heuristic extraction works for any source format.
- (+) Live ontology. Adding shots improves future extraction immediately.
- (+) Telegram-ready output. Presentable text formatted for human consumption.
- (-) Heuristic extraction has edge cases (compensation regex, city detection). Acceptable.
- (-) Live ontology enrichment adds 1-3s latency on shot upload. Background task mitigates.
