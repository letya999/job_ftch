---
title: "Ontology compiler and runtime projection"
description: "How labeled shots become tenant ontology, graph terms, and legacy runtime tables."
updated: 2026-07-27
---
# Ontology compiler and runtime projection

This document is the canonical reference for profile ontology behavior. Read it
before changing `job_ftch/application/ontology_compiler.py`,
`ontology_graph_builder.py`, ontology stores, or relevance prompts.

## Purpose

The ontology is learned per tenant/profile from labeled shots: positive/negative
resumes and positive/negative jobs. It is not an AI-jobs dictionary, and it must
not contain Python-side domain vocabularies such as "python is good" or
"manager is bad". The same compiler must work for another hiring domain from
that tenant's shots.

For a new profession/profile, do not treat ontology/relevance as production-ready
until the profile has at least 48 labelled shots: 12 negative resume shots, 12
positive resume shots, 12 positive vacancy shots, and 12 negative vacancy
shots. These shots are separate from the larger labelled prefilter training
dataset; both are required before using a profession-specific hard gate in
production.

The ontology has two layers:

1. **Compiled ontology**: the semantic source of truth. It stores accepted and
   rejected decisions with evidence, confidence, weights, polarity, scope, and
   graph relations.
2. **Legacy projection**: compatibility tables consumed by newer runtime code:
   roles, skills, seniority, anti patterns, positive keywords, and negative
   keywords. These tables are derived from compiled terms and must not become a
   separate source of truth.

## Compile Flow

`scripts/mvp_data_repair.py rebuild-ontology` reads the active tenant profile
shots and calls `compile_ontology_from_shots`.

The compiler performs bounded LLM passes:

1. **Candidate extraction** over chunks of labeled shots.
2. **Coverage extraction** over smaller chunks, to recover concrete named tools
   and boundary terms that a compact compile pass may summarize away.
3. **Profile compile** over a compact candidate table.
4. **Optional critique** for consistency checks.

Prompts live in `config/prompts/ontology_compiler_v2.yaml`. Python code only
loads prompts, validates structured output, normalizes mechanics, deduplicates,
stores, and projects. Domain relevance decisions must come from structured LLM
output and evidence, not from Python dictionaries or regex vocabularies.

## Compiled Term Contract

Each compiled term should carry:

- `canonical`: normalized display term.
- `aliases`: alternate names from evidence.
- `entity_type`: `role`, `skill`, `keyword`, `anti_pattern`, or `seniority`.
- `semantic_role`: `target_role`, `target_skill`, `supporting_skill`,
  `anti_role`, `anti_skill`, `negative_keyword`, `anti_pattern`, etc.
- `polarity`: `positive`, `negative`, `contextual`, or `neutral`.
- `scope`: `target`, `supporting`, `anti`, `background`, `current`, `past`,
  `desired`, etc.
- `source_section`: where the evidence came from: `title`, `desired_role`,
  `current_role`, `past_role`, `requirements`, `skills`, `summary`,
  `anti_reason`, and so on.
- `evidence_shot_ids`: required for accepted terms.
- `support_count`, `contrast_count`, `confidence`, `weight`.
- `reject_reason` for rejected or contextual terms.

Important semantic rules:

- Past resume roles do not become target roles unless current/desired/title
  evidence explicitly supports them.
- One accepted term cannot be both positive and negative in the same compiled
  ontology.
- The same canonical surface can still have a contextual contrast. For example,
  `rag` can be a positive target skill, while a negative shot may produce the
  anti pattern `insufficient rag`. Do not materialize both positive and negative
  legacy rows with the same canonical term.
- Accepted terms without evidence are invalid and are removed by sanitization.

## Negative Terms

Do not assume negative ontology terms must be emitted by the LLM as
`anti_skill` or `negative_keyword`. In practice, a negative shot often says that
a candidate/job lacks a target capability; the LLM may return that as
`entity_type=skill`, `semantic_role=target_skill`, `polarity=negative`.

Projection must treat `polarity=negative` plus negative evidence as a valid
negative signal. If the same canonical term is also positive, materialize an
anti pattern such as `insufficient <term>` to preserve the contrast without
positive/negative overlap.

## Positive Skills And Keywords

The compiler should recover concrete named tools and capabilities from shots,
including single-shot named tools when the evidence is focused. However, not
every positive skill should also become a positive keyword.

Runtime projection intentionally keeps these surfaces separate:

- `positive_skills`: wider skill vocabulary for profile matching and prompt
  context.
- `positive_keywords`: compact high-signal compatibility list for legacy
  keyword-style scoring.
- `negative_keywords` and `anti_patterns`: boundary evidence from negative
  shots, including "insufficient X" contrasts.

Avoid "fixing" precision by adding domain stoplists. Tune projection by
evidence shape: polarity, source section, support count, confidence, weight,
and per-shot evidence breadth.

## Storage Tables

Compiled source-of-truth tables:

- `jf_ontology_compiled_term`
- `jf_ontology_compiled_relation`
- `jf_ontology_node`
- `jf_ontology_edge`
- `jf_ontology_evidence`
- `jf_ontology_occurrence`
- `jf_ontology_term_stat`
- `jf_ontology_graph_version`

Legacy projection tables:

- `jf_ontology_role`
- `jf_ontology_skill`
- `jf_ontology_seniority`
- `jf_ontology_anti`
- `jf_ontology_positive_keyword`
- `jf_ontology_negative_keyword`

When rebuilding from a staging artifact with candidate chunks, the repair path
must ignore stale compiled accepted terms in that artifact and re-project from
candidate chunks using current code. This lets us fix materialization bugs
without paying for a new LLM extraction pass.

## Current Dev Baseline

After the 2026-07-26 rebalance on the active `ai_jobs` tenant:

```text
roles: 5
skills+: 90
positive_keywords: 48
negative_keywords: 23
anti: 18
```

400-sample dev eval on `fixtures/dataset/eval_dataset.jsonl`, seed `42`,
graph `config/pipelines/evidence_v2_compact_prefilter.yaml`:

```text
P=0.860
R=0.740
F1=0.796
LLM calls=89
cost ~$0.0619
```

A wider positive-only projection scored higher F1 but was rejected because it
collapsed negative ontology coverage:

```text
skills+: 112
positive_keywords: 117
negative_keywords: 1
anti: 1
```

## Developer Checklist

Before changing ontology behavior:

1. Read this document and `config/prompts/ontology_compiler_v2.yaml`.
2. Do not add domain vocabularies, regex relevance lists, or hardcoded role/skill
   decisions in Python.
3. Preserve evidence IDs for every accepted term.
4. Preserve positive/negative no-overlap invariants.
5. Keep compiled terms as source of truth and legacy tables as projections.
6. Run ontology tests:

```bash
uv run pytest tests/application/test_ontology_graph_builder.py tests/test_ontology_and_llm.py -q
uv run ruff check job_ftch/application/ontology_compiler.py tests/application/test_ontology_graph_builder.py
```

7. Rebuild dev ontology from the latest staging artifact when only projection
   code changed:

```bash
uv run python scripts/mvp_data_repair.py rebuild-ontology --apply --staging-artifact artifacts/mvp_data_repair/20260726T121731Z_ontology_extraction_staging.json
```

8. Run the 400-sample eval and compare P/R/F1, calls, cost, and ontology counts.
