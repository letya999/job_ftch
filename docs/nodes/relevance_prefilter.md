---
title: "TF-IDF + Logistic Regression Relevance Prefilter"
description: "Trainable TF-IDF/logistic-regression gate used before the LLM relevance judge."
updated: 2026-07-29
node_id: tfidf_logreg_prefilter
---

# TF-IDF + Logistic Regression Relevance Prefilter

## 1. What it is

A TF-IDF (word 1-2 grams, `min_df=2`, `sublinear_tf=True`) plus logistic
regression (`class_weight="balanced"`, `C=4.0`) model trained on a labelled
vacancy dataset. Inference uses pure Python and numpy - sklearn is NOT a
runtime dependency. It runs as a pipeline node before the LLM judge and drops
candidates unlikely to be relevant.

The pinned artifact is profile-specific:
`fixtures/prefilter/tfidf_logreg_v1.json` was trained and validated for the
current `ai_jobs` / AI-engineering recipe. It is a production default for that
recipe only. Do not treat it as a universal Data Engineer, backend, product,
analytics, or generic vacancy classifier.

Node id: `tfidf_logreg_prefilter`
Class: `TfidfLogregRelevancePrefilterNode`
Module: `job_ftch/nodes/tfidf_logreg_prefilter.py`
Artifact: `fixtures/prefilter/tfidf_logreg_v1.json`


## 2. Why it exists

Before this node existed, the LLM relevance judge was called on 97% of
candidates. Historical pre-recipe impact on the seed-42 sample of 400 labelled
items for the pinned prefilter family:

| Metric | Without prefilter | With prefilter |
|--------|------------------|----------------|
| LLM calls | 455 | 76 |
| Precision | 0.717 | 0.957 |
| Recall | 0.760 | 0.815 |
| F1 | 0.738 | 0.880 |
| Cost | ~$0.33 | $0.055 |

The current pinned production recipe is the source of truth for graph/model
hashes, the 40 shots, datasets, commands, and regression gates; see
[`docs/recipes/pipeline_recipe.md`](../recipes/pipeline_recipe.md) and
[`config/recipes/production_pipeline_recipe.yaml`](../../config/recipes/production_pipeline_recipe.yaml).
The current production graph uses `threshold: 0.20`, keeping the prefilter as
a hard negative gate while allowing more borderline candidates to reach the
LLM relevance judge. The 2026-07-29 controlled champion for that graph reports
`TP=44`, `FP=8`, `FN=10`, `TN=424`, `P=0.846`, `R=0.815`, `F1=0.830`.

Historical reference: on the pinned production live `/run` from 2026-07-26, the 17-source run
produced 763 sanitized candidates. The prefilter dropped 711 candidates,
leaving 34 relevance LLM calls, 31 terminal decision items, and 10 emitted
results at `$0.05498`. Manual labels over ACCEPT + REVIEW + REJECT gave
TP=10, TN=17, FP=0, FN=4 (P=1.000, R=0.714, F1=0.833) **for the terminal
decision set only**. Full live recall over all 763 sanitized items still
requires adjudicating prefilter/dedup/sanitize drops.


## 3. The rule: proves only a negative

The node proves only a negative: score below threshold means drop. It can
never accept a candidate - that responsibility belongs to `DecisionNode`.
Candidates that pass the prefilter still face the LLM judge, profile match,
evidence decision, and all downstream nodes.

For a new tenant/profile, the conservative default is to disable this node or
run it in shadow until you have profile-specific labelled data and held-out
recall evidence. A hard gate trained on the wrong profile can make the LLM
invisible to good candidates, producing high precision and poor end-to-end
recall.

New profile onboarding also needs enough labelled shots before a relevance
recipe is considered production-ready: at least 12 negative resume shots, 12
positive resume shots, 12 positive vacancy shots, and 12 negative vacancy
shots.


## 4. Without a dataset it does not work

If the model artifact (`fixtures/prefilter/tfidf_logreg_v1.json`) is missing,
unreadable, or has a wrong schema version, the node degrades to pass-through.
Every candidate reaches the LLM, costs rise, and quality falls toward the
historical no-prefilter baseline (P=0.717, F1=0.738 in the pre-recipe
measurement).

This is intentional: silently filtering on an absent or corrupt model is more
dangerous than paying for extra LLM calls.

How to detect degraded mode:

- **Preflight**: `--preflight-only` in `run_pipeline_eval.py` reports
  `relevance_prefilter.status` as `degraded:model_missing` (or
  `degraded:schema_version_mismatch` or `degraded:model_unreadable`).
  Exits non-zero unless `--allow-degraded-prefilter` is passed.
- **Metadata**: every item processed in degraded mode carries
  `relevance_prefilter_degradation` in its metadata (value is the reason
  string, not a boolean).
- **Runtime stats**: the node exposes `stats["items_degraded_passthrough"]`
  which is collected by the eval harness into `runtime_node_stats`. A run
  with a non-zero degraded passthrough count is distinguishable from one
  with a working prefilter.


## 5. How to train your own model

### Dataset requirements

- Format: JSONL, one record per line
- Required fields per record: `stable_id` (string), `text` (string),
  `relevant` (integer 0 or 1; strings and `"unknown"` are ignored)
- Minimum 2000 labelled rows
- Minimum 150 positive (relevant=1) rows
- Positive fraction between 0.02 and 0.50

### Build a dataset from manual live labels

If you manually labelled a live fetched snapshot, convert it to the training
JSONL format first:

```powershell
uv run python scripts/eval/build_prefilter_dataset_from_manual_labels.py `
  --candidates .runtime/release_eval/data_engineer_eval_candidates_340.jsonl `
  --labels .runtime/release_eval/manual_labels_data_engineer_340.json `
  --out fixtures/dataset/data_engineer_prefilter_seed.jsonl
```

For a production retrain this seed is not enough by itself: the training guard
requires 2000+ labelled rows and 150+ positives. Append more labelled live
snapshots before exporting a production artifact.

### Training command

```
.venv/Scripts/python.exe scripts/eval/train_relevance_prefilter.py \
  --dataset fixtures/dataset/your_dataset.jsonl \
  --exclude-ids fixtures/prefilter/holdout_seed42_sample400.txt \
  --out fixtures/prefilter/tfidf_logreg_data_engineer_v1.json \
  --threshold 0.30
```

The script:
1. Validates the dataset against minimum requirements (exits non-zero if
   they are not met).
2. Performs a stratified 80/20 train/holdout split.
3. Trains TF-IDF + logreg on the 80% training split.
4. Computes a threshold sweep on the 20% held-out split and checks that
   positive retention at the chosen threshold is at least 0.90.
5. If the retention check passes, refits on all data and exports the
   artifact.

The sweep output shows how many candidates and what fraction of positives
each threshold retains on held-out data. Use it to choose your threshold.


## 6. How to choose a threshold

The threshold is chosen by held-out positive retention, not by call count.
From measured sweeps and promoted recipe evidence:

| Threshold | Candidate retention evidence | Positive retention / outcome |
|-----------|------------------------------|------------------------------|
| 0.20 | current production recipe | controlled champion `P=0.846`, `R=0.815` |
| 0.30 | 88 | 0.96 |
| 0.35 | 77 | 0.96 |
| 0.45 | 67 | 0.90 |

The rule: target positive retention of at least 0.90 on the held-out split.
Below that, the prefilter starts dropping true positives that the LLM judge
would have accepted.

Lower thresholds are safer (fewer false drops) but send more candidates to
the LLM. Higher thresholds save more LLM calls but risk dropping relevant
items the LLM would have caught.


## 7. What this does not solve

Recall is capped by the LLM judge, not only by the prefilter. In the pinned
live terminal decision set, the 4 false negatives were review/reject outcomes
after candidate ingestion; full live recall still requires adjudicating
prefilter/dedup/sanitize drops. Raising the prefilter threshold will not
improve recall - it only saves calls and increases false-drop risk.


## 8. Another domain

The model learns entirely from the dataset. There are zero domain-specific
words in the node code. For a different vacancy domain or profile (e.g. Data
Engineer instead of AI/ML engineering), you need:

1. Your own labelled dataset (2000+ rows, 150+ positives)
2. Your own trained model artifact
3. A threshold chosen from your sweep
4. At least 48 profile shots: 12 negative resume, 12 positive resume,
   12 positive vacancy, and 12 negative vacancy shots

Do NOT reuse the pinned `ai_jobs` model artifact for a different profile - it
learned that recipe's vocabulary and may not generalize.

## 9. How to disable it for a tenant/run

Use a graph without `tfidf_logreg_prefilter`, for example:

```yaml
pipeline_graph_path: config/pipelines/evidence_v2_compact_postaccept.yaml
pipeline_graph_expected_hash: null
```

For temporary local verification, put that override in an ignored runtime YAML
and include it in `JOB_FTCH_RUNTIME_CONFIG_PATH` after `config/runtime.yaml` and
before adapter runtime overrides. For production, prefer a named tenant/profile
recipe with its own graph hash and release evidence.

**Important distinction**: the prefilter and ontology are separate components.
The prefilter is trained from the labeled eval dataset. The ontology compiler is
learned from tenant/profile shots through structured LLM extraction and
evidence-backed projection; see
[`docs/ontology/compiler.md`](../ontology/compiler.md). Do not add Python-side
AI-domain dictionaries to either layer.
