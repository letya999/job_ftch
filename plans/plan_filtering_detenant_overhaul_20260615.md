# Plan: Filtering Overhaul + De-tenant Bot + Tech Debt
Date: 2026-06-15

## Decisions (from user)
- Filtering: optimal hybrid — prefilter uses ONLY user profile (no default mixing); embeddings dominate final ranking; strengthen negatives.
- Embeddings: applied in final ranking (not prefilter — embeddings unavailable pre-extraction).
- Tenancy: remove tenant from ALL bot commands, use default tenant internally, record as tech debt.

## Root causes confirmed
1. `latest_jobs` truncates BEFORE reranking → post-run cards show 5 newest groups (DevOps/1C), not best matches.
2. `_build_runtime_catalog` mixes broad default `ai_roles` with user profile → prefilter passes DevOps/Data Analyst.
3. `ProfileWeights.vector = 0.1` → embeddings only 10% of score → hybrid search ineffective.
4. Negative embedding penalty thresholds too high (0.7/0.8) → rarely triggers.
5. Bot commands require/parse tenant_id everywhere → friction.

## Changes

### 1. job_ftch/application/tenant_runner.py

**1a. `_build_runtime_catalog`** — use ONLY user profiles when present:
```python
extra_profiles = tuple(sp for record in active_records for sp in record.profile.search_profiles)
if extra_profiles:
    return ProfileCatalog(catalog_name=f"{base_catalog.catalog_name}+user", profiles=extra_profiles)
return base_catalog
```

**1b. `latest_jobs`** — rerank-before-limit + optional min_score gate:
```python
async def latest_jobs(self, tenant_id, *, limit=10, user_id=None, profile_id=None, min_score=None):
    runtime = self.get_runtime(tenant_id)
    total = await runtime.job_group_store.count()
    pool = min(total or limit, max(limit * 10, 200))
    groups = await runtime.job_group_store.list_groups(limit=pool)
    ranked = await self._rerank_groups_for_profile(groups, tenant_id=tenant_id, user_id=user_id, profile_id=profile_id)
    jobs = [g.canonical_job for g in ranked]
    if min_score is not None and jobs and any(j.best_score is not None for j in jobs):
        jobs = [j for j in jobs if (j.best_score or 0.0) >= min_score]
    return jobs[:limit]
```

**1c. add `default_tenant_id()` helper:**
```python
def default_tenant_id(self) -> str:
    ids = self.tenant_ids()
    if not ids:
        raise RuntimeError("No tenants configured")
    return ids[0]
```

### 2. job_ftch/domain/profile.py — higher vector weight for user profiles
Do NOT change global defaults (tests depend). Instead set user-profile weights in profile_inputs.

### 3. job_ftch/application/profile_inputs.py
In `build_profile_from_resume_text_async`, when building search_profiles[0], set weights emphasizing vector:
```python
from job_ftch.domain.profile import ProfileWeights
...
search_profiles[0] = sp.model_copy(update={
    "allowed_languages": _normalize_language_codes(extracted.languages),
    "relevance_threshold": 0.35,
    "weights": ProfileWeights(title=0.15, semantic_role=0.1, skills=0.15, domain=0.08,
                              seniority=0.05, region=0.04, salary=0.03, culture=0.0, vector=0.4),
})
```
(sum = 1.0)

### 4. job_ftch/nodes/match_scoring.py — graded negative penalty
Replace:
```python
if max_neg_sim > 0.8: neg_vector_penalty = 0.5
elif max_neg_sim > 0.7: neg_vector_penalty = 0.2
```
With:
```python
if max_neg_sim > 0.78: neg_vector_penalty = 0.6
elif max_neg_sim > 0.68: neg_vector_penalty = 0.4
elif max_neg_sim > 0.58: neg_vector_penalty = 0.2
```

### 5. Bot handlers — remove tenant args

**adapters/telegram_bot/handlers/admin.py:**
- `/run` (no arg) → `tenant_id = runner.default_tenant_id()`, always single-tenant path with cards. Remove run_all.
- `/reset` (no arg) → default tenant.
- `/reset_dedup` (no arg) → default tenant.
- `/addsource <link>` → default tenant (link = args[0]).
- `/addsources <link...>` → default tenant.
- `/disablesource <source_id>` → default tenant.
- `/setposting <channel>` → default tenant.
- `/setnotify <mode> [batch]` → default tenant.
- Post-run cards: `latest_jobs(tenant_id, limit=config.digest_size, user_id, profile_id=f"user_{user_id}", min_score=0.3)`.

**adapters/telegram_bot/handlers/search_digest.py:**
- `/digest` (no arg) → default tenant; pass `min_score=0.3`. If empty, message "No strong matches yet. Upload more example resumes or run /run."
- `/search <query>` → default tenant.

**adapters/telegram_bot/handlers/base.py:**
- `/status`, `/sources` → default tenant, no arg parsing.
- Update help text usages (remove tenant_id from descriptions/usages).

### 6. Bot command descriptions (base.py _ADMIN_COMMANDS / main.py)
Update usage hints: "/run - Run the pipeline now", "/reset_dedup - Clear dedup (dev)", etc. No tenant mention.

### 7. Tech debt note
Append plans/TECH_DEBT.md:
- Multi-tenancy is hidden, not removed: bot assumes single default tenant. To restore multi-tenant UX, re-add tenant arg parsing in handlers.
- ozon.tech: api_sniffer needs playwright chromium not installed in image; source fails gracefully. Either `playwright install --with-deps chromium` in Dockerfile or disable source.
- SemanticPrefilter is keyword-only; embeddings not available pre-extraction. Consider a cheap embedding gate post-extraction.

## Verification
- `docker compose up -d --build`
- `docker compose exec bot python -c "..."` test latest_jobs returns AI roles on top, DevOps gated out by min_score.
- In bot: /run (no arg) → cards are AI-relevant. /digest → AI-relevant, no DevOps.
