from __future__ import annotations

from typing import TYPE_CHECKING

from job_ftch.nodes.match_scoring import _cosine_sim

if TYPE_CHECKING:
    from job_ftch.application.contracts import EmbeddingProvider
    from job_ftch.domain import RawItem
    from job_ftch.domain.profile import ProfileCatalog


class EmbeddingPrefilterNode:
    """
    Cross-lingual prefilter using embeddings anchored on the profile target_roles.

    Unlike a positive-example centroid, this works immediately because target_roles
    always exist. A Russian post embeds close to its English role anchor (JobBERT-v3
    is trained for cross-lingual job-title matching). Runs after SemanticPrefilterNode.
    """

    def __init__(
        self,
        catalog: ProfileCatalog,
        embedding_provider: EmbeddingProvider,
        *,
        pass_threshold: float = 0.50,
        drop_threshold: float = 0.35,
        max_chars: int = 512,
    ) -> None:
        self._catalog = catalog
        self._embedding_provider = embedding_provider
        self._pass_threshold = pass_threshold
        self._drop_threshold = drop_threshold
        self._max_chars = max_chars
        # profile_id -> tuple[tuple[float, ...], ...] (one vector per role); built lazily
        self._role_vectors: dict[str, tuple[tuple[float, ...], ...]] | None = None

    def _embed_fn(self):
        return getattr(
            self._embedding_provider,
            "embed_query",
            getattr(self._embedding_provider, "embed", None),
        )

    async def _ensure_role_vectors(self) -> None:
        if self._role_vectors is not None:
            return
        self._role_vectors = {}
        embed_fn = self._embed_fn()
        if embed_fn is None:
            return
        for profile in self._catalog.profiles:
            roles = [r for r in profile.target_roles if r and r.strip()]
            if not roles:
                continue
            try:
                vecs = await embed_fn(roles)
            except Exception:
                continue
            self._role_vectors[profile.profile_id] = tuple(tuple(v) for v in vecs)

    async def process(self, item: RawItem) -> RawItem | None:
        await self._ensure_role_vectors()
        if not self._role_vectors:
            return item  # no anchors -> no-op

        embed_fn = self._embed_fn()
        if embed_fn is None:
            return item
        try:
            vecs = await embed_fn([item.text[: self._max_chars]])
            if not vecs:
                return item
            text_vec = tuple(vecs[0])
        except Exception:
            return item  # fail-safe: never lose data on embedding errors

        best_sim = 0.0
        best_profile = "default"
        for profile_id, role_vecs in self._role_vectors.items():
            for rv in role_vecs:
                sim = _cosine_sim(text_vec, rv)
                if sim > best_sim:
                    best_sim = sim
                    best_profile = profile_id

        metadata = {
            **item.metadata,
            "embedding_role_match": round(best_sim, 4),
            "embedding_role_best_profile": best_profile,
        }

        if best_sim >= self._pass_threshold:
            decision = "pass"
        elif best_sim < self._drop_threshold:
            decision = "low_signal"
        else:
            decision = "uncertain"

        return item.model_copy(
            update={"metadata": {**metadata, "embedding_prefilter_decision": decision}}
        )
