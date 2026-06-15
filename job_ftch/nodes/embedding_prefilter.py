from __future__ import annotations

from typing import TYPE_CHECKING

from job_ftch.application.drops import RawItemDropped
from job_ftch.domain import TriageRejectionReason
from job_ftch.nodes.match_scoring import _cosine_sim

if TYPE_CHECKING:
    from job_ftch.application.contracts import EmbeddingProvider
    from job_ftch.domain import RawItem
    from job_ftch.domain.profile import ProfileCatalog


class EmbeddingPrefilterNode:
    """
    Prefilter node that uses embeddings to detect semantically distant items.
    Runs after SemanticPrefilterNode (keyword-based).
    """

    def __init__(self, catalog: ProfileCatalog, embedding_provider: EmbeddingProvider) -> None:
        self._catalog = catalog
        self._embedding_provider = embedding_provider

    async def process(self, item: RawItem) -> RawItem | None:
        profiles_with_vectors = [p for p in self._catalog.profiles if p.embedding_vector]
        if not profiles_with_vectors:
            return item  # no-op when no profile vectors

        try:
            # Handle different provider interface versions
            embed_fn = getattr(
                self._embedding_provider,
                "embed_query",
                getattr(self._embedding_provider, "embed", None),
            )
            if embed_fn is None:
                return item

            # Use first 4000 chars for embedding to avoid context limits
            vecs = await embed_fn([item.text[:4000]])
            if not vecs:
                return item
            text_vec = tuple(vecs[0])
        except Exception:
            # fail-safe: pass through on error to avoid losing data due to API issues
            return item

        sims = [_cosine_sim(text_vec, p.embedding_vector) for p in profiles_with_vectors]
        max_sim = max(sims) if sims else 0.0

        keyword_score = float(item.metadata.get("semantic_prefilter_best_score", "1.0") or "1.0")
        # We consider keyword prefilter "passed" if it was significantly above 0
        # (SemanticPrefilter usually requires > threshold * 0.75 internally to pass)
        threshold = max((p.relevance_threshold for p in self._catalog.profiles), default=0.4)
        keyword_passed = keyword_score >= threshold * 0.75

        metadata = {
            **item.metadata,
            "embedding_prefilter_max_sim": round(max_sim, 4),
        }

        if max_sim >= 0.45:
            # Strong embedding signal -> pass regardless of keyword
            return item.model_copy(
                update={"metadata": {**metadata, "embedding_prefilter_decision": "pass"}}
            )

        if max_sim < 0.28 and not keyword_passed:
            # Both signals say no -> drop
            raise RawItemDropped(
                reason=TriageRejectionReason.TELEGRAM_LOW_SIGNAL,
                details=f"Embedding prefilter: max_sim={max_sim:.3f} keyword_passed={keyword_passed}",
                item=item,
            )

        # Uncertain zone or keyword passed -> allow through
        return item.model_copy(
            update={"metadata": {**metadata, "embedding_prefilter_decision": "uncertain"}}
        )
