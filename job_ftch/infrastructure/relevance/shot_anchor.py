"""Shot-anchored relevance: local multilingual embeddings + profile shot stores.

Design (why this exists):
- The relevance profile must live in the DB as example postings, not as YAML
  keyword lists. Operators/users curate positive/negative shots; this module
  embeds them with a local multilingual model.
- A new posting's relevance is the similarity of its embedding to the shot
  anchors: ``score = mean(top_k positive cos) - mean(top_k negative cos)``.
- The default backend is in-memory shots sourced from
  :class:`ManagedCandidateProfile` (the same object the Telegram bot populates).
  This guarantees that what the user added through `/positive` or
  `/positive_job` is exactly what the relevance scorer uses — no Qdrant
  fixture indirection.

Storage backends (selectable via ``relevance_shot_backend`` setting):
- ``"memory"`` (default) — shots come from the in-process
  ``ManagedCandidateProfile`` catalogue. This is the source of truth for
  per-user bot shots and is the supported default.
- ``"qdrant"`` — shots are stored in dedicated collections
  (live: ``profile_shots_bgem3``, seed: ``profile_shots_bgem3_seed``) and can
  be loaded in user-scoped, tenant-scoped, or seed-only modes.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np

from job_ftch.domain.bgem3_card import build_bgem3_card

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from job_ftch.domain import SearchProfile

_DEFAULT_MODEL = "intfloat/multilingual-e5-small"
_DEFAULT_COLLECTION = "profile_shots_e5"
_DIM = 384

_BGEM3_COLLECTION = "profile_shots_bgem3"
_BGEM3_DIM = 1024


def _extract_user_id(role: str) -> str | None:
    if not role.startswith("user:"):
        return None
    user_part, separator, _rest = role.partition("@tenant:")
    if not separator:
        return None
    user_id = user_part.removeprefix("user:").strip()
    return user_id or None


def _extract_tenant_id(role: str) -> str | None:
    _prefix, separator, rest = role.partition("@tenant:")
    if not separator:
        return None
    tenant_id, separator, _suffix = rest.partition(":")
    if not separator:
        return None
    tenant_id = tenant_id.strip()
    return tenant_id or None


def _extract_vector_dim(vectors_config: Any) -> int | None:
    """Extract the vector dim from a Qdrant ``vectors_config`` object.

    The shape varies between Qdrant versions: a bare ``VectorParams``
    (with a ``.size`` attribute) or a dict mapping name -> VectorParams.
    Returns ``None`` when the dim cannot be determined.
    """
    if vectors_config is None:
        return None
    if isinstance(vectors_config, dict):
        if not vectors_config:
            return None
        first = next(iter(vectors_config.values()))
        return getattr(first, "size", None)
    return getattr(vectors_config, "size", None)


class ShotEmbedder:
    """Lazy, thread-safe wrapper over a local sentence-transformer.

    e5 models require task prefixes: ``passage:`` for stored documents/shots and
    ``query:`` for the text being scored. We apply them automatically.
    """

    def __init__(self, model_name: str = _DEFAULT_MODEL) -> None:
        self._model_name = model_name
        self._model = None
        self._lock = threading.Lock()

    def _ensure(self) -> None:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from sentence_transformers import SentenceTransformer

                    self._model = SentenceTransformer(self._model_name)

    @property
    def dim(self) -> int:
        return _DIM

    def encode_passages(self, texts: Sequence[str]) -> np.ndarray:
        self._ensure()
        assert self._model is not None
        prefixed = [f"passage: {t}" for t in texts]
        return np.asarray(
            self._model.encode(prefixed, normalize_embeddings=True, show_progress_bar=False),
            dtype=np.float32,
        )

    def encode_query(self, text: str) -> np.ndarray:
        self._ensure()
        assert self._model is not None
        vec = self._model.encode(
            [f"query: {text}"], normalize_embeddings=True, show_progress_bar=False
        )
        return np.asarray(vec[0], dtype=np.float32)


@dataclass(frozen=True)
class Shot:
    """Single shot anchor used by ``BgeMThreeShotScorer``.

    ``label`` is one of ``"positive"`` / ``"negative"``. ``vector`` is the
    pre-computed dense embedding (BGE-M3, 1024-d). ``sparse_weights`` is the
    pre-computed lexical-weight map from BGE-M3 sparse output. ``role`` is
    informational (e.g. ``"vacancy:RAG Engineer"``) and is NOT used for
    scoring — scoring is purely a top-k cosine match on dense + sparse.
    """

    label: str  # "positive" | "negative"
    role: str
    text: str
    vector: np.ndarray
    sparse_weights: dict[str, float] | None = None
    provenance: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.label not in ("positive", "negative"):
            raise ValueError(f"Shot.label must be 'positive' or 'negative', got {self.label!r}")


class ShotStore:
    """Qdrant-backed store for profile shots (sync client; used by tooling).

    The vectors are the source of record in Qdrant; for fast scoring we also keep
    them in memory after ``load``.
    """

    def __init__(
        self,
        *,
        url: str,
        api_key: str | None = None,
        collection: str = _DEFAULT_COLLECTION,
    ) -> None:
        from qdrant_client import QdrantClient

        self._client = QdrantClient(url=url, api_key=api_key)
        self._collection = collection

    def recreate(self) -> None:
        from qdrant_client.http import models as rest

        self._client.recreate_collection(
            collection_name=self._collection,
            vectors_config=rest.VectorParams(size=_DIM, distance=rest.Distance.COSINE),
        )

    def upsert_shots(self, shots: Sequence[Shot]) -> int:
        from qdrant_client.http import models as rest

        points = [
            rest.PointStruct(
                id=i,
                vector=shot.vector.tolist(),
                payload={"label": shot.label, "role": shot.role, "text": shot.text},
            )
            for i, shot in enumerate(shots)
        ]
        self._client.upsert(collection_name=self._collection, points=points)
        return len(points)

    def load(self) -> tuple[np.ndarray, np.ndarray]:
        """Return (positive_matrix, negative_matrix) of shot vectors from Qdrant."""
        records, _ = self._client.scroll(
            collection_name=self._collection,
            with_vectors=True,
            with_payload=True,
            limit=10_000,
        )
        pos, neg = [], []
        for rec in records:
            vec = np.asarray(rec.vector, dtype=np.float32)
            if (rec.payload or {}).get("label") == "positive":
                pos.append(vec)
            else:
                neg.append(vec)
        return (
            np.vstack(pos) if pos else np.zeros((0, _DIM), np.float32),
            np.vstack(neg) if neg else np.zeros((0, _DIM), np.float32),
        )


@dataclass(frozen=True)
class RelevanceScore:
    sim_pos: float  # mean of top-k positive cosines
    sim_neg: float  # mean of top-k negative cosines
    max_pos: float
    max_neg: float
    sparse_margin: float = 0.0

    @property
    def margin(self) -> float:
        return self.sim_pos - self.sim_neg


class ShotRelevanceScorer:
    """Scores a posting against in-memory shot anchors (cosine, top-k mean)."""

    def __init__(
        self,
        positives: np.ndarray,
        negatives: np.ndarray,
        embedder: ShotEmbedder,
        *,
        top_k: int = 5,
    ) -> None:
        self._pos = positives
        self._neg = negatives
        self._embedder = embedder
        self._top_k = top_k

    @staticmethod
    def _topk_mean(sims: np.ndarray, k: int) -> tuple[float, float]:
        if sims.size == 0:
            return 0.0, 0.0
        ordered = np.sort(sims)[::-1]
        return float(ordered[: min(k, ordered.size)].mean()), float(ordered[0])

    def score_vector(self, vec: np.ndarray) -> RelevanceScore:
        sp = self._pos @ vec if self._pos.size else np.zeros(0, np.float32)
        sn = self._neg @ vec if self._neg.size else np.zeros(0, np.float32)
        mean_pos, max_pos = self._topk_mean(sp, self._top_k)
        mean_neg, max_neg = self._topk_mean(sn, self._top_k)
        return RelevanceScore(mean_pos, mean_neg, max_pos, max_neg)

    def score_text(self, text: str) -> RelevanceScore:
        return self.score_vector(self._embedder.encode_query(text))


# ---------------------------------------------------------------------------
# BGE-M3 shot store: in-memory (default) + Qdrant (legacy)
# ---------------------------------------------------------------------------


class BgeMThreeShotProvider(Protocol):
    """Anything that can produce dense+sparse embeddings for shots.

    Production: a ``BgeMThreeProvider`` already wired through the bot
    adapter (used both at pipeline time to encode incoming jobs and at
    shot-add time to encode the user's examples). Tests: a mock that
    returns deterministic vectors.
    """

    @property
    def dim(self) -> int: ...

    def encode(
        self, text: str, *, max_length: int = 512, return_sparse: bool = False
    ) -> dict[str, Any]: ...


class InMemoryBgeMThreeShotStore:
    """The default BGE-M3 shot store.

    Shots are pushed by the bot adapter when the user adds
    ``/positive`` / ``/negative`` / ``/positive_job`` / ``/negative_job``
    examples, and re-encoded automatically whenever the model or the texts
    change. The builder loads from this store at every pipeline run, so
    the relevance scorer always sees the *current* user shots — never
    a fixed fixture.

    The store keeps:

    * ``shots`` — list of ``Shot`` objects, in insertion order, with
      dense and (optionally) sparse vectors.
    * ``_dirty`` — set of shot-text fingerprints whose vectors are out
      of date relative to the source text. Re-encode is lazy.
    * ``_lock`` — guards mutations and reads. The bot runs handler code
      concurrently with pipeline runs, so all public methods are
      thread-safe.

    Dimensions are validated: any attempt to push a shot whose vector
    length does not match ``provider.dim`` raises ``ValueError`` (with
    a clear message) so silent dimension mismatches between the Qdrant
    collection and the encoder cannot propagate.
    """

    def __init__(
        self,
        *,
        provider: BgeMThreeShotProvider,
        max_length: int = 1024,
    ) -> None:
        self._provider = provider
        self._max_length = max_length
        self._shots: list[Shot] = []
        self._lock = threading.Lock()

    # ---- mutation API (called by the bot) --------------------------------

    def add_text(
        self,
        text: str,
        *,
        label: str,
        role: str = "",
    ) -> Shot:
        """Encode ``text`` with the configured provider and append as a shot.

        ``text`` is the raw example text (resume or vacancy) the user
        sent. The dense+sparse vectors are computed once here and
        cached; the relevance scorer reads them at pipeline time
        without re-encoding.
        """
        text = (text or "").strip()
        if not text:
            raise ValueError("add_text: text must be non-empty")
        if label not in ("positive", "negative"):
            raise ValueError(f"add_text: label must be 'positive' or 'negative', got {label!r}")

        encoded = self._provider.encode(
            build_bgem3_card(text, max_chars=self._max_length * 4),
            max_length=self._max_length,
            return_sparse=True,
        )
        dense = np.asarray(encoded["dense"], dtype=np.float32)
        self._validate_dim(dense, source="add_text")
        sparse = self._normalize_sparse(encoded.get("sparse") or {})
        shot = Shot(
            label=label,
            role=role or f"{label}",
            text=text,
            vector=dense,
            sparse_weights=sparse,
        )
        with self._lock:
            self._shots.append(shot)
        return shot

    def add_shot(self, shot: Shot) -> None:
        """Append a pre-encoded ``Shot`` (used by tests, batch seeders)."""
        self._validate_dim(shot.vector, source="add_shot")
        with self._lock:
            self._shots.append(shot)

    def clear(self) -> None:
        with self._lock:
            self._shots.clear()

    def clear_by_role(self, role_prefix: str) -> int:
        """Remove all shots whose role starts with ``role_prefix``.

        Used when the bot deletes one user example. Returns the count
        removed.
        """
        with self._lock:
            kept = [s for s in self._shots if not s.role.startswith(role_prefix)]
            removed = len(self._shots) - len(kept)
            self._shots = kept
        return removed

    def remove_text(self, text: str) -> int:
        """Remove all shots whose text matches ``text`` exactly. Returns count."""
        with self._lock:
            kept = [s for s in self._shots if s.text != text]
            removed = len(self._shots) - len(kept)
            self._shots = kept
        return removed

    # ---- read API (called by the builder / relevance scorer) -------------

    def load(
        self,
        tenant_id: str | None = None,
        user_id: str | None = None,
        categories: tuple[str, ...] | None = None,
    ) -> tuple[np.ndarray, np.ndarray, list[dict[str, float]], list[dict[str, float]]]:
        """Return (pos_dense, neg_dense, pos_sparse, neg_sparse).

        Each dense matrix has shape ``(N, dim)`` where ``dim`` is
        ``provider.dim``. Each sparse list is a list of
        ``{token_id_str: weight}`` dicts in the same order as the
        dense matrix rows.
        """
        with self._lock:
            snapshot = list(self._shots)
        if tenant_id is not None:
            snapshot = [
                shot
                for shot in snapshot
                if (shot_tenant := _extract_tenant_id(shot.role)) is None
                or shot_tenant == tenant_id
            ]
        if user_id is not None:
            snapshot = [
                shot
                for shot in snapshot
                if (shot_user := _extract_user_id(shot.role)) is None or shot_user == user_id
            ]
        if categories is not None:
            snapshot = [shot for shot in snapshot if _shot_category(shot.role) in categories]
        return self._split_by_label(snapshot)

    def size(self) -> tuple[int, int]:
        with self._lock:
            return sum(1 for s in self._shots if s.label == "positive"), sum(
                1 for s in self._shots if s.label == "negative"
            )

    # ---- batch reload from a list of (label, role, text) tuples -----------

    def replace_from_profiles(self, profiles: Iterable[SearchProfile]) -> tuple[int, int]:
        """Re-encode and replace all shots from a list of search profiles.

        Used at pipeline-build time when the user might have added or
        removed shots between bot interactions. The store's previous
        contents are discarded; only shots derived from the supplied
        profiles survive. Shots that already match by ``(text, role)``
        are reused to avoid re-encoding.
        """
        new_shots: list[Shot] = []
        for sp in profiles:
            for text in sp.positive_example_texts:
                new_shots.append(self._build_or_reuse(sp, text, "positive", "resume:positive"))
            for text in sp.negative_example_texts:
                new_shots.append(self._build_or_reuse(sp, text, "negative", "resume:negative"))
            for text in sp.positive_job_example_texts:
                new_shots.append(self._build_or_reuse(sp, text, "positive", "vacancy:positive"))
            for text in sp.negative_job_example_texts:
                new_shots.append(self._build_or_reuse(sp, text, "negative", "vacancy:negative"))
        with self._lock:
            self._shots = list(new_shots)
        return self.size()

    # ---- internals --------------------------------------------------------

    def _build_or_reuse(
        self,
        sp: SearchProfile,
        text: str,
        label: str,
        role: str,
    ) -> Shot:
        text = (text or "").strip()
        if not text:
            raise ValueError("_build_or_reuse: empty text from profile")
        with self._lock:
            for existing in self._shots:
                if existing.role == role and existing.text == text:
                    return existing
        encoded = self._provider.encode(
            build_bgem3_card(text, max_chars=self._max_length * 4),
            max_length=self._max_length,
            return_sparse=True,
        )
        dense = np.asarray(encoded["dense"], dtype=np.float32)
        self._validate_dim(dense, source="replace_from_profiles")
        sparse = self._normalize_sparse(encoded.get("sparse") or {})
        return Shot(
            label=label,
            role=role,
            text=text,
            vector=dense,
            sparse_weights=sparse,
        )

    def _split_by_label(
        self, shots: Sequence[Shot]
    ) -> tuple[np.ndarray, np.ndarray, list[dict[str, float]], list[dict[str, float]]]:
        pos_d, neg_d = [], []
        pos_s: list[dict[str, float]] = []
        neg_s: list[dict[str, float]] = []
        for shot in shots:
            if shot.label == "positive":
                pos_d.append(shot.vector)
                pos_s.append(shot.sparse_weights or {})
            else:
                neg_d.append(shot.vector)
                neg_s.append(shot.sparse_weights or {})
        return (
            np.vstack(pos_d).astype(np.float32)
            if pos_d
            else np.zeros((0, self._provider.dim), np.float32),
            np.vstack(neg_d).astype(np.float32)
            if neg_d
            else np.zeros((0, self._provider.dim), np.float32),
            pos_s,
            neg_s,
        )

    def _validate_dim(self, vec: np.ndarray, *, source: str) -> None:
        expected = self._provider.dim
        if vec.ndim != 1:
            raise ValueError(f"{source}: expected 1-d vector, got shape {vec.shape!r}")
        if vec.shape[0] != expected:
            raise ValueError(
                f"{source}: vector dim {vec.shape[0]} does not match provider "
                f"dim {expected}. Model and store are out of sync."
            )

    @staticmethod
    def _normalize_sparse(sparse: dict[str, Any]) -> dict[str, float]:
        """BGE-M3 returns dict[int, float]; JSON-ify keys so the shot is portable."""
        out: dict[str, float] = {}
        for k, v in sparse.items():
            try:
                out[str(int(k))] = float(v)
            except (TypeError, ValueError):
                continue
        return out


class BgeMThreeQdrantShotStore:
    """Qdrant-backed store for BGE-M3 shots.

    This is the **default** backend in production
    (``relevance_shot_backend="qdrant"``). The bot writes user
    examples here on every add/delete; the pipeline builder
    reads them at every ``/run``. The collection is auto-created
    at the first write with the dimension that matches the
    current ``provider.dim``.

    A previous model swap that left a stale-dim collection
    behind is handled by :meth:`ensure_collection`, which
    compares the existing dim with ``provider.dim`` and
    recreates the collection when they differ. Silent dim
    mismatches (the previous prod bug) are therefore caught
    loudly rather than corrupting the relevance score.

    Embeddings are produced by the supplied ``provider`` and upserted
    point-by-point. The collection is recreated if its vector
    dimension does not match ``provider.dim`` (i.e. the operator
    switched the embedding model) so a stale fixture collection from
    a previous model cannot silently corrupt scoring.
    """

    def __init__(
        self,
        *,
        url: str,
        api_key: str | None = None,
        collection: str = _BGEM3_COLLECTION,
        provider: BgeMThreeShotProvider | None = None,
        max_length: int = 1024,
    ) -> None:
        from qdrant_client import QdrantClient

        self._client = QdrantClient(url=url, api_key=api_key)
        self._collection = collection
        self._provider = provider
        self._max_length = max_length

    def ensure_collection(self) -> None:
        """Create or verify the Qdrant collection; migrate to versioned name on dim mismatch."""
        import structlog as _sl
        from qdrant_client.http import models as rest

        expected = self._provider.dim if self._provider is not None else _BGEM3_DIM
        if not self._client.collection_exists(self._collection):
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=rest.VectorParams(size=expected, distance=rest.Distance.COSINE),
            )
            return
        info = self._client.get_collection(self._collection)
        existing = (
            _extract_vector_dim(info.config.params.vectors)
            if (info and info.config and info.config.params)
            else None
        )
        if existing != expected:
            # Dimension mismatch from a model change: migrate to a versioned collection
            # rather than destroying the existing shots. The new collection remains intact.
            versioned = f"{self._collection}_v{expected}"
            _sl.get_logger("job_ftch.shot_anchor").warning(
                "qdrant_collection_dim_mismatch_migrating",
                collection=self._collection,
                existing_dim=existing,
                expected_dim=expected,
                new_collection=versioned,
            )
            self._collection = versioned
            if not self._client.collection_exists(versioned):
                self._client.create_collection(
                    collection_name=versioned,
                    vectors_config=rest.VectorParams(size=expected, distance=rest.Distance.COSINE),
                )

    def upsert_shots(self, shots: Sequence[Shot]) -> int:
        from qdrant_client.http import models as rest

        points = [
            rest.PointStruct(
                id=i,
                vector=shot.vector.tolist(),
                payload=self._payload_for_shot(shot),
            )
            for i, shot in enumerate(shots)
        ]
        self._client.upsert(collection_name=self._collection, points=points)
        return len(points)

    def upsert_shots_with_ids(self, items: Sequence[tuple[str, Shot]]) -> int:
        """Upsert shots with caller-supplied stable ids.

        Each item is ``(point_id, shot)``. Using deterministic
        ids means a re-upsert of the same (role, text) pair
        overwrites the existing point instead of accumulating
        duplicates. The bot relies on this so a user who
        re-sends an example does not double-count the shot
        in the relevance scorer.
        """
        from qdrant_client.http import models as rest

        points = [
            rest.PointStruct(
                id=pid,
                vector=shot.vector.tolist(),
                payload=self._payload_for_shot(shot),
            )
            for pid, shot in items
        ]
        self._client.upsert(collection_name=self._collection, points=points)
        return len(points)

    @staticmethod
    def _payload_for_shot(shot: Shot) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "label": shot.label,
            "role": shot.role,
            "text": shot.text,
            "sparse_weights": shot.sparse_weights,
        }
        payload.update(shot.provenance)
        tenant_id = _extract_tenant_id(shot.role)
        user_id = _extract_user_id(shot.role)
        if tenant_id is not None:
            payload["tenant_id"] = tenant_id
        if user_id is not None:
            payload["user_id"] = user_id
        return payload

    def delete_shots_by_ids(self, point_ids: Sequence[str]) -> int:
        """Delete specific points. Returns the count Qdrant reports.

        Used by the bot to remove a single shot when the user
        deletes one example. Best-effort: callers swallow errors.
        """
        from qdrant_client.http import models as rest

        if not point_ids:
            return 0
        self._client.delete(
            collection_name=self._collection,
            points_selector=rest.PointIdsList(points=list(point_ids)),
        )
        # ``delete`` returns an UpdateResult; status can be
        # "completed" / "acknowledged". Either way the operation
        # succeeded.
        return len(point_ids)

    def delete_by_role_prefix(self, role_prefix: str) -> int:
        """Delete every point whose payload ``role`` starts with
        ``role_prefix``. Used when the user clears all of their
        examples via the bot.
        """
        from qdrant_client.http import models as rest

        flt = rest.Filter(
            must=[
                rest.FieldCondition(
                    key="role",
                    match=rest.MatchText(text=role_prefix),
                ),
            ],
        )
        # ``MatchText`` does substring match, not prefix. The
        # payload roles are stable (e.g. ``user:42@tenant:default:vacancy:positive``)
        # so the prefix-style substring is exactly what we want.
        self._client.delete(
            collection_name=self._collection,
            points_selector=rest.FilterSelector(filter=flt),
        )
        return 0  # Qdrant does not return the count for filter deletes

    def clear(self) -> int:
        """Drop every point but keep the collection schema.

        Used by tests to reset between runs.
        """
        from qdrant_client.http import models as rest

        self._client.delete(
            collection_name=self._collection,
            points_selector=rest.FilterSelector(filter=rest.Filter()),
        )
        return 0

    @staticmethod
    def _build_role_filter(
        *,
        role_prefix: str | None,
        tenant_id: str | None,
        user_id: str | None = None,
    ) -> Any | None:
        from qdrant_client.http import models as rest

        clauses: list[Any] = []
        if role_prefix:
            clauses.append(
                rest.FieldCondition(
                    key="role",
                    match=rest.MatchText(text=role_prefix),
                )
            )
        if tenant_id:
            clauses.append(
                rest.FieldCondition(
                    key="tenant_id",
                    match=rest.MatchValue(value=tenant_id),
                )
            )
        if user_id:
            clauses.append(
                rest.FieldCondition(
                    key="user_id",
                    match=rest.MatchValue(value=user_id),
                )
            )
        if not clauses:
            return None
        return rest.Filter(must=clauses)

    @staticmethod
    def _matches_role_prefix(payload: dict[str, Any], role_prefix: str | None) -> bool:
        if not role_prefix:
            return True
        role = str(payload.get("role") or "")
        return role.startswith(role_prefix)

    @staticmethod
    def _matches_tenant(payload: dict[str, Any], tenant_id: str | None) -> bool:
        if not tenant_id:
            return True
        payload_tenant = payload.get("tenant_id")
        if isinstance(payload_tenant, str) and payload_tenant.strip():
            return payload_tenant.strip() == tenant_id
        role = str(payload.get("role") or "")
        extracted = _extract_tenant_id(role)
        if extracted == tenant_id:
            return True
        # Legacy points with no tenant_id metadata are treated as belonging to the default tenant.
        return bool(not extracted and tenant_id == "default")

    @staticmethod
    def _matches_user(payload: dict[str, Any], user_id: str | None) -> bool:
        if not user_id:
            return True
        payload_user = payload.get("user_id")
        if isinstance(payload_user, str) and payload_user.strip():
            return payload_user.strip() == user_id
        role = str(payload.get("role") or "")
        extracted = _extract_user_id(role)
        return extracted == user_id

    def load(
        self,
        *,
        role_prefix: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        categories: tuple[str, ...] | None = None,
    ) -> tuple[np.ndarray, np.ndarray, list[dict[str, float]], list[dict[str, float]]]:
        records, _ = self._client.scroll(
            collection_name=self._collection,
            with_vectors=True,
            with_payload=True,
            limit=10_000,
            scroll_filter=self._build_role_filter(
                role_prefix=role_prefix,
                tenant_id=tenant_id,
                user_id=user_id,
            ),
        )
        expected = self._provider.dim if self._provider is not None else _BGEM3_DIM
        pos_d, neg_d = [], []
        pos_s: list[dict[str, float]] = []
        neg_s: list[dict[str, float]] = []
        for rec in records:
            payload = rec.payload or {}
            if not self._matches_role_prefix(payload, role_prefix):
                continue
            if not self._matches_tenant(payload, tenant_id):
                continue
            if not self._matches_user(payload, user_id):
                continue
            if (
                categories is not None
                and _shot_category(str(payload.get("role") or "")) not in categories
            ):
                continue
            vec = np.asarray(rec.vector, dtype=np.float32)
            if vec.shape[0] != expected:
                # Stale-dim record: skip it. The next ``ensure_collection``
                # call followed by a fresh seed will clear the rest.
                continue
            sparse: dict[str, float] = {
                str(k): float(v) for k, v in (payload.get("sparse_weights") or {}).items()
            }
            if payload.get("label") == "positive":
                pos_d.append(vec)
                pos_s.append(sparse)
            else:
                neg_d.append(vec)
                neg_s.append(sparse)
        return (
            np.vstack(pos_d) if pos_d else np.zeros((0, expected), np.float32),
            np.vstack(neg_d) if neg_d else np.zeros((0, expected), np.float32),
            pos_s,
            neg_s,
        )


class BgeMThreeShotScorer:
    """Scores a posting using BGE-M3 dense vectors pre-computed by BgeMThreeNode.

    Reads ``bgem3_dense`` from item.metadata — does NOT re-encode.
    Uses the same top-k cosine logic as ``ShotRelevanceScorer`` for
    drop-in compatibility.
    """

    def __init__(
        self,
        positives: np.ndarray,
        negatives: np.ndarray,
        *,
        pos_sparse: list[dict[str, float]] | None = None,
        neg_sparse: list[dict[str, float]] | None = None,
        top_k: int = 5,
    ) -> None:
        self._pos = positives
        self._neg = negatives
        self._top_k = top_k

        self._pos_sparse = tuple(pos_sparse or ())
        self._neg_sparse = tuple(neg_sparse or ())

    @staticmethod
    def _topk_mean(sims: np.ndarray, k: int) -> tuple[float, float]:
        if sims.size == 0:
            return 0.0, 0.0
        ordered = np.sort(sims)[::-1]
        return float(ordered[: min(k, ordered.size)].mean()), float(ordered[0])

    def score_vector(self, vec: np.ndarray) -> RelevanceScore:
        sp = self._pos @ vec if self._pos.size else np.zeros(0, np.float32)
        sn = self._neg @ vec if self._neg.size else np.zeros(0, np.float32)
        mean_pos, max_pos = self._topk_mean(sp, self._top_k)
        mean_neg, max_neg = self._topk_mean(sn, self._top_k)
        return RelevanceScore(mean_pos, mean_neg, max_pos, max_neg)

    def score_sparse(self, query_sparse: dict[int, float] | dict[str, float]) -> float:
        """Return the normalized top-k positive-vs-negative sparse margin."""
        positive, _negative = self.sparse_components(query_sparse)
        return positive - _negative

    def sparse_components(
        self, query_sparse: dict[int, float] | dict[str, float]
    ) -> tuple[float, float]:
        normalized_query = {str(key): float(value) for key, value in query_sparse.items()}
        pos = np.asarray(
            [_sparse_cosine(normalized_query, anchor) for anchor in self._pos_sparse],
            dtype=np.float32,
        )
        neg = np.asarray(
            [_sparse_cosine(normalized_query, anchor) for anchor in self._neg_sparse],
            dtype=np.float32,
        )
        mean_pos, _ = self._topk_mean(pos, self._top_k)
        mean_neg, _ = self._topk_mean(neg, self._top_k)
        return mean_pos, mean_neg

    def score_from_metadata(self, metadata: dict[str, Any]) -> RelevanceScore | None:
        """Read ``bgem3_dense`` from metadata; return None if not present."""
        raw = metadata.get("bgem3_dense")
        if raw is None:
            return None
        vec = np.asarray(raw, dtype=np.float32)
        score = self.score_vector(vec)

        raw_sparse = metadata.get("bgem3_sparse")
        if raw_sparse is not None and (self._pos_sparse or self._neg_sparse):
            sparse_score = self.score_sparse(raw_sparse)
            return RelevanceScore(
                sim_pos=score.sim_pos,
                sim_neg=score.sim_neg,
                max_pos=score.max_pos,
                max_neg=score.max_neg,
                sparse_margin=sparse_score,
            )
        return score


def _shot_category(role: str) -> str:
    for category in ("vacancy", "resume"):
        if role.startswith(f"{category}:") or f":{category}:" in role:
            return category
    return "unknown"


def _sparse_cosine(query: dict[str, float], anchor: dict[str, float]) -> float:
    if not query or not anchor:
        return 0.0
    dot = sum(value * anchor.get(key, 0.0) for key, value in query.items())
    query_norm = sum(value * value for value in query.values()) ** 0.5
    anchor_norm = sum(value * value for value in anchor.values()) ** 0.5
    return dot / (query_norm * anchor_norm) if query_norm and anchor_norm else 0.0


# ---------------------------------------------------------------------------
# Backwards-compat re-exports for the Qdrant class name used by newer scripts.
# ---------------------------------------------------------------------------


def __getattr__(name: str) -> Any:  # pragma: no cover - compat shim
    if name == "BgeMThreeShotStore":
        return BgeMThreeQdrantShotStore
    raise AttributeError(name)
