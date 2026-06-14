# Plan: MVP Bot Batch 2 — fastembed Adapter + Pipeline Fix + Vector Scoring

## Goal
Add local embedding support without OpenAI:
1. `fastembed` adapter registered as `embedding_provider=fastembed`
2. Fix pipeline order: EmbeddingNode runs BEFORE MultiProfileMatchNode
3. EmbeddingNode stores job vector in job.metadata["embedding_vector"] for inline scoring
4. MultiProfileMatchNode uses cosine similarity between job vector and profile vectors
5. SearchProfile stores precomputed embedding_vector from example texts
6. Update docs/tech_stack.md with fastembed entry

## Architecture constraints (MUST follow)
- `domain/` zero imports outside pydantic + stdlib — tuple[float, ...] fields are OK
- `nodes/` can only import `domain/` + `application/` — cosine sim must be pure Python (math only)
- New adapter self-registers via `@register_embedding_provider("fastembed")` decorator
- New dependency → update `docs/tech_stack.md` FIRST (per AGENTS.md rule)
- Commits: feat, fix, chore, docs, refactor only. NO Co-authored-by. NO AI attribution.
- All tests must pass after changes

---

## BLOCK 0: Update docs/tech_stack.md FIRST

In `docs/tech_stack.md`, under "LLM и извлечение" table, add new row:

| `fastembed` | `[fastembed]` | MVP | Локальные мультиязычные ONNX-эмбеддинги без GPU (альтернатива sentence-transformers) |

---

## BLOCK 1: fastembed dependency

### `pyproject.toml`
In `[project.optional-dependencies]`, add new group:
```toml
fastembed = ["fastembed>=0.3"]
```
Also add `"fastembed"` to the `all` group if it exists (the group that includes all extras).

DO NOT add fastembed to `[project.dependencies]` — it must stay optional.

---

## BLOCK 2: fastembed EmbeddingProvider adapter

### New file: `job_ftch/infrastructure/llm/fastembed_provider.py`

```python
"""FastEmbed-based local embedding provider (ONNX, no GPU required)."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from job_ftch.application.registry import register_embedding_provider

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)

_DEFAULT_MODEL = "intfloat/multilingual-e5-small"  # 384 dims, RU/EN/KZ


@register_embedding_provider("fastembed")
def _create_fastembed_provider(settings: object) -> FastEmbedProvider:
    model_name = getattr(settings, "embedding_model", None) or _DEFAULT_MODEL
    return FastEmbedProvider(model_name=model_name)


class FastEmbedProvider:
    """EmbeddingProvider backed by fastembed ONNX models (local, no API key)."""

    def __init__(self, model_name: str = _DEFAULT_MODEL) -> None:
        self._model_name = model_name
        self._model: object | None = None

    def _get_model(self) -> object:
        if self._model is None:
            from fastembed import TextEmbedding  # type: ignore[import]
            self._model = TextEmbedding(model_name=self._model_name)
            logger.info("fastembed_model_loaded", model=self._model_name)
        return self._model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed list of texts. Runs sync fastembed in thread executor."""
        if not texts:
            return []
        loop = asyncio.get_running_loop()
        def _sync_embed() -> list[list[float]]:
            model = self._get_model()
            return [vec.tolist() for vec in model.embed(texts)]  # type: ignore[union-attr]
        return await loop.run_in_executor(None, _sync_embed)
```

### Register via auto-import in `job_ftch/application/registry.py`

Find the `_load_builtin_adapters()` function (or `load_extensions()` or similar function that
imports builtin adapters). Add to its body:
```python
try:
    import job_ftch.infrastructure.llm.fastembed_provider  # noqa: F401
except ImportError:
    pass  # fastembed extras not installed
```

Check that `@register_embedding_provider` decorator is defined in `registry.py`. Look for
`_embedding_provider_factories` dict and `register_embedding_provider` function. If they exist
but the import isn't there, just add the try/import above.

---

## BLOCK 3: Domain changes — profile embedding vectors

### `job_ftch/domain/profile.py`

**Note: BLOCK 1 of Batch 1 already added `positive_example_texts` and `negative_example_texts`
to SearchProfile. This block ADDS embedding vector fields.**

Add to `SearchProfile` class (after `negative_example_texts`, before `relevance_threshold`):
```python
embedding_vector: tuple[float, ...] | None = None
negative_embedding_vectors: tuple[tuple[float, ...], ...] = ()
```
These are pure tuples of floats — valid in domain/.

The `normalize` validator does NOT need to touch these fields (they're already normalized floats).

---

## BLOCK 4: Profile embed-on-save in adapters layer

### `job_ftch/adapters/profile_inputs.py`

Add new async function `embed_profile_examples`:
```python
async def embed_profile_examples(
    managed: ManagedCandidateProfile,
    embedding_provider: object,  # EmbeddingProvider Protocol
) -> ManagedCandidateProfile:
    """Compute embedding vectors for profile's example texts and store them."""
    from datetime import UTC, datetime

    if not managed.profile.search_profiles:
        return managed

    updated_profiles = list(managed.profile.search_profiles)
    for i, sp in enumerate(updated_profiles):
        pos_texts = list(sp.positive_example_texts) + (
            [managed.profile.resume.raw_text] if managed.profile.resume and managed.profile.resume.raw_text else []
        )
        neg_texts = list(sp.negative_example_texts)

        pos_vector: tuple[float, ...] | None = None
        neg_vectors: tuple[tuple[float, ...], ...] = ()

        if pos_texts:
            try:
                vecs = await embedding_provider.embed(pos_texts)
                if vecs:
                    # Average positive example vectors
                    dim = len(vecs[0])
                    avg = [sum(v[d] for v in vecs) / len(vecs) for d in range(dim)]
                    pos_vector = tuple(avg)
            except Exception:
                pass  # embedding failed, skip

        if neg_texts:
            try:
                vecs = await embedding_provider.embed(neg_texts)
                neg_vectors = tuple(tuple(v) for v in vecs)
            except Exception:
                pass

        updated_profiles[i] = sp.model_copy(update={
            "embedding_vector": pos_vector,
            "negative_embedding_vectors": neg_vectors,
        })

    updated_profile = managed.profile.model_copy(
        update={"search_profiles": tuple(updated_profiles)}
    )
    return ManagedCandidateProfile(
        user_id=managed.user_id,
        profile_id=managed.profile_id,
        profile=updated_profile,
        updated_at=datetime.now(UTC),
    )
```

### `job_ftch/adapters/telegram_bot/bot.py`

After saving a profile in `handle_document()`, if `embedding_enabled` and provider available:
- Import and call `embed_profile_examples(managed_profile, self._embedding_provider)`
- `self._embedding_provider` comes from `TelegramBotService.__init__` if configured
- If not configured, skip silently

Add optional `embedding_provider: object | None = None` parameter to `TelegramBotService.__init__`.

---

## BLOCK 5: Fix pipeline order + vector score in EmbeddingNode

### `job_ftch/application/builder.py`

In `build_nodes()` function (around line 442), change EmbeddingNode placement:

BEFORE (current — wrong):
```python
nodes: list[Stage[Any, Any]] = [
    SourceContextNode(),
    ...
    MultiProfileMatchNode(catalog),  # line ~456
    RiskScoringNode(),
    ...
]
if settings.embedding_enabled and settings.vector_backend:
    ...
    nodes.append(EmbeddingNode(...))  # appended at END — WRONG
```

AFTER (correct):
```python
nodes: list[Stage[Any, Any]] = [
    SourceContextNode(),
    PostTypeClassificationNode(classifier=classifier),
    HardFilterNode(catalog),
    DedupNode(store),
    SemanticPrefilterNode(catalog),
    ExtractionNode(llm),
    ExtractionValidationNode(),
    TitleCompanyNormalizationNode(normalizer),
    SkillNormalizationNode(normalizer),
    LocationWorkModeNormalizationNode(),
    CompensationParsingNode(),
    JobLifecycleNode(),
    JobAggregationNode(job_group_store, attach_group_id=True),
]

# Insert EmbeddingNode BEFORE MultiProfileMatchNode so vector is available for scoring
if settings.embedding_enabled and settings.vector_backend:
    provider = cast("EmbeddingProvider", create_embedding_provider(settings))
    vector_backend = cast("VectorBackend", create_vector_backend(settings))
    if provider and vector_backend:
        from job_ftch.nodes.embedding import EmbeddingNode
        nodes.append(EmbeddingNode(provider=provider, vector_backend=vector_backend))

nodes.extend([
    MultiProfileMatchNode(catalog),
    RiskScoringNode(),
    QualityScoringNode(),
    JobValidationNode(),
    RoutingNode(
        accept_threshold=settings.routing_accept_threshold,
        review_threshold=settings.routing_review_threshold,
        quality_override_threshold=settings.routing_quality_override_threshold,
    ),
])
```

### `job_ftch/nodes/embedding.py`

After upserting to vector_backend, also store vector on job metadata:
```python
# After: await self.vector_backend.upsert(...)
updated_metadata = {**job.metadata, "embedding_vector": vectors[0]}
return job.model_copy(update={"metadata": updated_metadata})
```

Change return type accordingly: the function already returns `JobRecord`, just make sure
`job.model_copy(...)` is returned instead of `return job` at the end.

The full `process()` method becomes:
```python
async def process(self, job: JobRecord) -> JobRecord:
    group_id = job.group_id or job.metadata.get("group_id")
    if not group_id:
        raise ValueError("group_id is required in job.metadata for EmbeddingNode")
    text = build_job_embedding_text(job)
    if not text:
        return job
    try:
        vectors = await self.provider.embed([text])
        if vectors and vectors[0]:
            payload: dict[str, object] = { ... }  # keep existing payload dict
            await self.vector_backend.upsert(
                job_id=job.stable_id,
                vector=vectors[0],
                payload=payload,
            )
            # Store vector on job for inline scoring by MultiProfileMatchNode
            updated_metadata = {**job.metadata, "embedding_vector": vectors[0]}
            return job.model_copy(update={"metadata": updated_metadata})
    except Exception as e:
        self._logger.warning("embedding_failed", job_id=job.stable_id, error=str(e))
    return job
```

---

## BLOCK 6: Vector scoring in MultiProfileMatchNode

### `job_ftch/nodes/match_scoring.py`

Add cosine similarity helper (pure Python, only `math` from stdlib):
```python
import math

def _cosine_sim(a: list[float], b: tuple[float, ...]) -> float:
    """Cosine similarity between job vector (list) and profile vector (tuple)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))
```

In `ProfileWeights` class (`domain/profile.py`), add:
```python
vector: float = Field(default=0.15, ge=0.0, le=1.0)
```
And reduce `skills` from 0.25 to 0.15 and `semantic_role` from 0.20 to 0.15 to keep total ~1.0:
- title: 0.20 (unchanged)
- semantic_role: 0.15 (was 0.20)
- skills: 0.20 (was 0.25)
- domain: 0.10 (unchanged)
- seniority: 0.10 (unchanged)
- region: 0.10 (unchanged)
- salary: 0.03 (unchanged)
- culture: 0.02 (unchanged)
- vector: 0.10 (NEW — when embedding available)
Total: 1.00

In `MultiProfileMatchNode._score_profile()`, add vector scoring:
```python
# After computing existing scores (before weighted = ...)
job_vec: list[float] | None = item.metadata.get("embedding_vector")  # type: ignore[assignment]
vector_score = 0.0
neg_vector_penalty = 0.0
if job_vec and profile.embedding_vector:
    vector_score = _cosine_sim(job_vec, profile.embedding_vector)
if job_vec and profile.negative_embedding_vectors:
    neg_vector_penalty = max(
        _cosine_sim(job_vec, neg_vec)
        for neg_vec in profile.negative_embedding_vectors
    )

weighted = (
    profile.weights.title * title_score
    + profile.weights.semantic_role * semantic_role_score
    + profile.weights.skills * skills_score
    + profile.weights.domain * domain_score
    + profile.weights.seniority * seniority_score
    + profile.weights.region * region_score
    + profile.weights.salary * salary_score
    + profile.weights.culture * culture_score
    + profile.weights.vector * vector_score
    - 0.10 * neg_vector_penalty  # penalty for similarity to negative examples
)
```

Also add `vector_score` and `neg_vector_penalty` to `ProfileMatchScore` if those fields exist
(check `job_ftch/domain/models.py` for the `ProfileMatchScore` dataclass). If they don't exist
yet, add them as optional fields with defaults:
```python
vector_score: float = 0.0
neg_vector_penalty: float = 0.0
```

And update the explanation string:
```python
explanation = (
    f"title={title_score:.2f} semantic={semantic_role_score:.2f} skills={skills_score:.2f} "
    f"domain={domain_score:.2f} seniority={seniority_score:.2f} region={region_score:.2f} "
    f"vector={vector_score:.2f} neg_penalty={neg_vector_penalty:.2f}"
)
```

---

## Summary of files to change

| File | Change |
|---|---|
| `docs/tech_stack.md` | Add fastembed row — DO THIS FIRST |
| `pyproject.toml` | Add `fastembed = ["fastembed>=0.3"]` extra group |
| `job_ftch/infrastructure/llm/fastembed_provider.py` | NEW — FastEmbedProvider class |
| `job_ftch/application/registry.py` | Auto-import fastembed_provider |
| `job_ftch/domain/profile.py` | Add `embedding_vector`, `negative_embedding_vectors`, `weights.vector` to SearchProfile/ProfileWeights |
| `job_ftch/adapters/profile_inputs.py` | Add `embed_profile_examples()` async function |
| `job_ftch/adapters/telegram_bot/bot.py` | Add optional `embedding_provider` to TelegramBotService |
| `job_ftch/application/builder.py` | Move EmbeddingNode BEFORE MultiProfileMatchNode |
| `job_ftch/nodes/embedding.py` | Store vector in job.metadata after upsert |
| `job_ftch/nodes/match_scoring.py` | Add `_cosine_sim`, vector_score, neg_penalty |
| `job_ftch/domain/models.py` | Add optional fields to ProfileMatchScore if missing |

---

## Verification after implementation
1. `python -m ruff check job_ftch/`
2. `python -m mypy job_ftch/`
3. `python -m pytest tests/ -x -q`

All must pass. If embedding extras not installed, tests must still pass (fastembed is optional).
Commit message: `feat(embeddings): fastembed adapter + pipeline vector scoring`
