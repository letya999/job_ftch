# Technical Debt Registry

Items deferred from active development. Review when relevant scope opens.

---

## TD-001: Machine-readable job posting contract

Define a formal schema for what constitutes a confirmed `job_posting`:
- hiring_intent_present (boolean, from keywords)
- role_identified (boolean)
- employer_identifiable (boolean)
- apply_path_exists (boolean, URL or email or t.me link)

Use this as the gating contract for output, not just `post_type == job_posting`.

**Priority:** High — prevents non-job content from leaking into output
**Effort:** Medium (1-2 days)
**Blocks:** TD-002

---

## TD-002: Golden dataset + eval harness

Collect and annotate:
- 50 confirmed real vacancies (positive)
- 50 non-vacancies from Telegram channels (announcements, digests, events)
- 20 edge cases (referral posts, "we're hiring" without detail, project-seeking-dev)
- 10 dead URLs
- 10 Telegram career channels
- 10 career site pages

Run eval after every pipeline change to track:
- classification_precision, classification_recall
- false_positive_rate (non-job sent to user)
- llm_calls_per_100_items
- valid_url_rate

**Priority:** High — only way to catch regressions without manual checking
**Effort:** High (2-3 days for dataset, 1 day for harness)
**Depends on:** TD-001

---

## TD-003: Separate triage layer with TriageDecision

Replace the current implicit classification flow with an explicit `TriageDecision` dataclass:

```python
class TriageDecision:
    content_type: Literal["job_posting", "announcement", "digest", "event", "article", "candidate", "spam", "unknown"]
    confidence: float
    reject_reason: str | None
    should_call_llm: bool
    evidence: list[str]
```

Move all fast classification (keyword, regex, source-kind rules) into one `TriageNode`
that produces this decision. Downstream nodes consume it instead of re-doing classification.

**Priority:** Medium
**Effort:** High (architectural refactor)

---

## TD-004: Source adapter contract

Different source types need different pipeline assumptions:
- `career_site`: always a job posting; validate URL; extract structured fields
- `telegram_channel`: mixed content; strict triage; cheap classifier first
- `telegram_group/comment`: very noisy; highest triage bar
- `rss`: usually structured; minimal LLM needed

Define `SourceAdapter.infer_content_type()` and `SourceAdapter.source_confidence()`
so the pipeline can self-configure per source kind.

**Priority:** Medium
**Effort:** High

---

## TD-005: DB schema normalization

Currently `jf_job_groups.raw_json` is the source of truth for all job fields.
Expression indexes and JSON path queries work, but won't scale past ~50k groups.

Recommended: extract key columns from raw_json to real table columns:
- `post_type TEXT`
- `source_kind TEXT`
- `best_score FLOAT`
- `quality_score FLOAT`
- `title TEXT`
- `company TEXT`
- `canonical_url TEXT`
- `updated_at TIMESTAMPTZ` (already exists)

Keep `raw_json` as debug payload only.

**Priority:** Low now, High at 10k+ groups
**Effort:** High (migration, model changes, query updates)

---

## TD-006: Source health scoring

Track per-source metrics in `jf_source_health` table:
- `job_posting_rate`: fraction of fetched items that became real jobs
- `duplicate_rate`
- `dead_url_rate`
- `llm_cost_per_valid_job`
- `last_success_at`

Expose as `/sources health` bot command. Use to auto-adjust triage strictness.

**Priority:** Low
**Effort:** Medium

---

## TD-007: Quarantine / /review command

Add a third item state between "drop" and "emit": `quarantine`.

Uncertain items (low-confidence post_type, valid URL but bad extraction) go to
`jf_quarantine_items` table instead of being silently dropped.

Add `/review` bot command to page through quarantined items and mark as
positive/negative example with one tap.

**Priority:** Medium — useful for improving profile from real edge cases
**Effort:** Medium (1 new table, 1 new bot command, FSM for review flow)

---

## TD-008: Feedback buttons on vacancy cards

Add inline keyboard to each sent vacancy card:
- ✅ Подходит
- ❌ Не вакансия
- ❌ Не мой профиль
- 🔗 Мёртвая ссылка

Save to `jf_user_feedback(job_id, user_id, feedback_type, sent_at)`.
Use feedback automatically as `/positive` / `/negative` signals.

**Priority:** High (user value, closes the feedback loop)
**Effort:** Medium (2 days: table, callback handlers, profile update integration)

---

## TD-009: Explainability / /debug_last_run

After each /run, store a debug summary per item:
- why_sent: post_type, profile_score, matched_skills, url_status
- why_dropped: content_type, evidence tokens, drop_reason

Expose via `/debug` command that shows last 5 dropped + last 5 sent with reasons.

**Priority:** Low
**Effort:** Medium

---

## TD-010: Admin backfill/reclassify commands

CLI commands for maintaining data quality over time:
- `job-ftch admin reclassify --where post_type is null`
- `job-ftch admin purge-non-jobs`
- `job-ftch admin rebuild-groups`
- `job-ftch admin recompute-scores --profile user_x`

**Priority:** Low
**Effort:** Low (1 day)

---

## TD-011: Concurrency hardening (advanced)

Current: per-user run lock (TTL-based), upload lock.
Missing:
- Optimistic locking for profile save (lost update race on concurrent /positive)
- Idempotency key for uploaded documents (same PDF twice = 1 example, not 2)
- Callback versioning (old inline buttons referencing deleted FSM state)

**Priority:** Medium (will surface when concurrent users increase)
**Effort:** Medium

---

## TD-012: Known good / strict mode flag

`STRICT_MODE=true` environment flag that overrides:
- `BOT_SEND_LIMIT_PER_RUN=5`
- `BOT_MIN_QUALITY_SCORE=0.5`
- `PIPELINE_MAX_LLM_CALLS=15`
- `ALLOW_UNKNOWN_POST_TYPE=false`

Use when classifier is unstable (e.g. after adding new sources).

**Priority:** Low
**Effort:** Low (1 hour)

---

## TD-013: Source snapshot table + incremental diff

Current snapshot diff is stored in the KV store (`snapshot:{tenant}:{source}:latest`).
The `jf_source_snapshots` table migration exists but is not yet used by the code.

Next steps:
1. Migrate `SnapshotFilterNode` and `TenantStore` snapshot methods to use `jf_source_snapshots`.
2. Add `Store` protocol methods for snapshots so in-memory/sqlite/postgres implement them natively.
3. Support historical diff queries ("what changed since yesterday").

**Priority:** Medium
**Effort:** Medium (1 day)

