# Plan: Funnel Quality Overhaul — 2026-06-15

## Goal
Fix three root causes of garbage in pipeline output and missed relevant informal posts:
1. `soft_preferences` never populated → prefilter ignores user skills → low recall
2. Token-overlap stop-words inflate title scores → DevOps passes as AI Engineer
3. `vacancy_type_score +0.1` bonus inflates ALL job_postings above threshold

Plus add embedding-based prefilter to catch informal text (Telegram posts) that keyword-matching misses.

## Files to modify / create

### 1. `job_ftch/application/profile_inputs.py`

#### In `build_candidate_profile_from_payload` (around line 194):
Populate `soft_preferences` from the union of `required_skills + preferred_skills` canonical names, so the SemanticPrefilterNode can see the user's skills during keyword prefiltering.

Add default anti-preferences list for AI-focused profiles. When building `SearchProfile`, if `anti_preferences` is empty, set it to a sensible default tuple:
`("1c", "devops", "hr manager", "recruiter", "sap", "accounting", "sales manager", "support engineer", "qa engineer")`

NOTE: Only add these defaults if the profile already has target_roles containing AI/ML terms (check: any of the target_roles contains "engineer", "developer", "scientist", "analyst" or target_domains contains "ai", "ml", "llm"). OR simply always set these defaults in this function (they apply to job_ftch's primary use case).

#### Change `build_candidate_profile_from_payload`:
```python
# After computing required_skills and preferred_skills names, add:
soft_pref_names = tuple(dict.fromkeys(
    list(required_skills) + list(preferred_skills)
))

search_profile = SearchProfile(
    ...
    soft_preferences=soft_pref_names,  # ADD THIS
    anti_preferences=anti_preferences or (
        "1c", "devops", "hr manager", "recruiter", "sap",
        "accounting", "sales manager", "support engineer",
    ),  # CHANGE THIS (was just anti_preferences)
    ...
)
```

### 2. `job_ftch/nodes/semantic_prefilter.py`

#### Fix profile_description bonus (around line 80):
Require at least 2 distinct non-trivial tokens (len > 3) from profile_description to appear in the text before awarding the bonus. Reduce bonus from 0.15 to 0.1.

```python
if profile.profile_description:
    desc_tokens = [
        t for t in profile.profile_description.casefold().split()
        if len(t) > 3
    ]
    matching = sum(1 for t in desc_tokens if t in lowered_text)
    if matching >= 2:
        profile_desc_bonus = 0.1  # was 0.15, now requires 2+ tokens
```

#### Also add fallback: check `required_skills` if `soft_preferences` is empty:
In `_score_profile`, after computing `soft_score`:
```python
# Fallback: if profile has no soft_preferences, use required_skills names
if not profile.soft_preferences and profile.required_skills:
    skill_names = tuple(s.canonical_name for s in profile.required_skills)
    soft_score = _overlap_score(tokens, skill_names)
```

This ensures old profiles (saved before this fix) also benefit.

### 3. NEW FILE: `job_ftch/nodes/embedding_prefilter.py`

Create `EmbeddingPrefilterNode` that runs AFTER `SemanticPrefilterNode`.

The node should:
- Accept `embedding_provider` and `catalog: ProfileCatalog`
- On `process(item: RawItem)`:
  - Check if any profile in catalog has `embedding_vector` set (not None, not empty)
  - If NO profile has an embedding vector → pass through unchanged (no-op)
  - If embedding vector exists:
    - Call `embedding_provider.embed_query([item.text[:4000]])` (or `embed` method) to get text embedding
    - Compute cosine similarity between text embedding and each profile's embedding_vector
    - Take max cosine similarity across profiles
    - If max_sim >= 0.45 → add metadata `embedding_prefilter_score`, `embedding_prefilter_decision=pass` and return item
    - If max_sim >= 0.28 → uncertain, return item with decision=uncertain in metadata
    - If max_sim < 0.28 → the item is semantically distant from all profiles
      - BUT only drop if we also have a semantic_prefilter_best_score in metadata < threshold (i.e. keyword also failed)
      - If keyword prefilter already passed (score in metadata >= threshold), keep item regardless
      - If BOTH keyword AND embedding say "no" → raise RawItemDropped

The node must handle exceptions gracefully: if embedding API fails, pass item through unchanged (do not drop).

```python
# Key logic sketch:
class EmbeddingPrefilterNode:
    def __init__(self, catalog: ProfileCatalog, embedding_provider) -> None:
        self._catalog = catalog
        self._embedding_provider = embedding_provider
    
    async def process(self, item: RawItem) -> RawItem | None:
        profiles_with_vectors = [p for p in self._catalog.profiles if p.embedding_vector]
        if not profiles_with_vectors:
            return item  # no-op when no profile vectors
        
        try:
            embed_fn = getattr(self._embedding_provider, 'embed_query', 
                               getattr(self._embedding_provider, 'embed', None))
            if embed_fn is None:
                return item
            vecs = await embed_fn([item.text[:4000]])
            if not vecs:
                return item
            text_vec = tuple(vecs[0])
        except Exception:
            return item  # fail-safe: pass through on error
        
        sims = [_cosine_sim(text_vec, p.embedding_vector) for p in profiles_with_vectors]
        max_sim = max(sims) if sims else 0.0
        
        keyword_score = float(item.metadata.get("semantic_prefilter_best_score", "1.0") or "1.0")
        threshold = max(p.relevance_threshold for p in self._catalog.profiles)
        keyword_passed = keyword_score >= threshold * 0.75
        
        metadata = {
            **item.metadata,
            "embedding_prefilter_max_sim": round(max_sim, 4),
        }
        
        if max_sim >= 0.45:
            # Strong embedding signal → pass regardless of keyword
            return item.model_copy(update={"metadata": {**metadata, "embedding_prefilter_decision": "pass"}})
        
        if max_sim < 0.28 and not keyword_passed:
            # Both signals say no → drop
            from job_ftch.application.drops import RawItemDropped
            from job_ftch.domain import TriageRejectionReason
            raise RawItemDropped(
                reason=TriageRejectionReason.TELEGRAM_LOW_SIGNAL,
                details=f"Embedding prefilter: max_sim={max_sim:.3f} keyword_passed={keyword_passed}",
                item=item,
            )
        
        # Uncertain zone or keyword passed → allow through
        return item.model_copy(update={"metadata": {**metadata, "embedding_prefilter_decision": "uncertain"}})
```

Import `_cosine_sim` from `match_scoring.py` or copy the implementation.

### 4. `job_ftch/nodes/__init__.py`

Export `EmbeddingPrefilterNode` from the package (add to imports and `__all__`).

### 5. `job_ftch/application/builder.py`

In `build_nodes` function (around line 450), after `SemanticPrefilterNode(catalog)`:

```python
# After SemanticPrefilterNode
if settings.embedding_enabled:
    try:
        emb_provider = cast("EmbeddingProvider", create_embedding_provider(settings))
        if emb_provider is not None:
            from job_ftch.nodes.embedding_prefilter import EmbeddingPrefilterNode
            nodes.append(EmbeddingPrefilterNode(catalog, emb_provider))
    except Exception:
        pass  # embedding not configured, skip
```

Place AFTER SemanticPrefilterNode entry (after line 450) and BEFORE ExtractionNode (line 451).

Note: The existing EmbeddingNode (around line 477-482) still runs after extraction for Qdrant storage — that's separate and should stay.

### 6. `job_ftch/nodes/match_scoring.py`

#### Fix A: Role stop-words in `_string_overlap_score`
Add before the function definition:
```python
_ROLE_STOP_WORDS = frozenset({
    "engineer", "developer", "manager", "specialist", "analyst",
    "lead", "senior", "junior", "head", "chief", "officer",
    "architect", "consultant", "expert", "professional", "associate",
    "intern", "staff", "principal",
})
```

In the partial token overlap branch (lines 45-49), after computing `option_tokens`:
```python
option_tokens = [token.casefold() for token in _TOKEN_RE.findall(option)]
# Only use discriminating tokens (non-stop-words) for overlap
discriminating = [t for t in option_tokens if t not in _ROLE_STOP_WORDS]
tokens_to_check = discriminating if discriminating else option_tokens
if not tokens_to_check or not value_tokens:
    continue
overlap = sum(1 for token in tokens_to_check if token in value_tokens) / len(tokens_to_check)
best = max(best, overlap)
```

This ensures "DevOps Engineer" does NOT match "AI Engineer" just because of "engineer".
"AI" vs {"devops","engineer"}: discriminating=["ai"], "ai" not in value → overlap=0.0 ✓
"ML Engineer" vs {"ml","engineer"}: discriminating=["ml"], "ml" in value → overlap=1.0 ✓

#### Fix B: Remove `vacancy_type_score` bonus OR reduce to 0.02

In `_score_profile` (around line 159-165):
```python
# Change:
final_score = max(0.0, min(1.0, round(weighted + 0.1 * vacancy_type_score - risk_penalty - neg_vector_penalty, 2)))
# To:
final_score = max(0.0, min(1.0, round(weighted - risk_penalty - neg_vector_penalty, 2)))
```

The PostTypeClassificationNode + HardFilterNode already handle filtering non-job-postings upstream. The +0.1 bonus was inflating scores of all job_postings by 10pp, pushing borderline irrelevant jobs above threshold.

Keep `vacancy_type_score` in the `ProfileMatchScore` dataclass for observability, just don't add it to `final_score`.

#### Fix C: `semantic_role_score` — remove full-description lookup
Change lines 108-112 from:
```python
semantic_role_score = max(
    title_score,
    _string_overlap_score(item.role_family, profile.target_roles),
    _string_overlap_score(role_text, profile.target_roles + profile.target_domains),
)
```
To:
```python
semantic_role_score = max(
    title_score,
    _string_overlap_score(item.role_family, profile.target_roles),
)
```

`role_text` included the full description (3000+ chars) making false positives common. Use only structured extracted fields: title_normalized and role_family.

### 7. Bot handlers: raise min_score

In `adapters/telegram_bot/handlers/search_digest.py`:
- Change `min_score=0.3` to `min_score=0.40` in `latest_jobs` calls

In `adapters/telegram_bot/handlers/admin.py`:
- Change `min_score=0.3` to `min_score=0.40` in `/run` card sending

### 8. Tests to update

#### `tests/nodes/test_match_scoring.py`
- The test at line 127 checks `neg_vector_penalty == 0.6` — this is unchanged, keep
- Add NEW test: `test_match_scoring_engineer_stopword_no_cross_match` — test that "DevOps Engineer" scores 0 title_score against profile with target_roles=["AI Engineer"]
- Add NEW test: `test_match_scoring_no_vacancy_bonus` — confirm that vacancy_type_score does NOT add to final_score

#### `tests/nodes/test_semantic_prefilter.py` (if exists, or create)
- Test that profile with skills (required_skills set, soft_preferences empty) still scores the Yandex-like post via skill-name fallback
- Test that description bonus requires 2+ tokens

After all code changes, run:
```
uv run pytest tests/ -x -q 2>&1 | tail -30
```

Then rebuild Docker:
```
docker compose up -d --build bot 2>&1 | tail -20
docker compose logs bot --tail=20
```

## Implementation Order

1. Fix `match_scoring.py` (stop-words, remove vacancy bonus, fix semantic_role_score)
2. Fix `semantic_prefilter.py` (required_skills fallback, desc bonus threshold)
3. Fix `profile_inputs.py` (soft_preferences, default anti_prefs)
4. Create `nodes/embedding_prefilter.py`
5. Update `nodes/__init__.py`
6. Wire in `builder.py`
7. Update bot min_score in handlers
8. Run tests, fix any failures
9. docker compose up -d --build bot

## Expected outcome

After these changes:
- Yandex informal post ("LLM-as-judge", "агентов", "AI-агента") → soft_score via skills → PASS prefilter → LLM extracts role → scoring gives 0.5+
- DevOps Engineer post → title_score = 0.0 (stop-word fix), no vacancy bonus → score ~0.1 → REJECT
- All job_postings: no free +0.1 bonus → cleaner score distribution
- Existing profiles without embedding_vector: EmbeddingPrefilterNode is a no-op → no regression
