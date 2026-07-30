"""Concrete managed-shot backend used by the application shot sync use-case."""

from __future__ import annotations

import asyncio
import hashlib
import threading
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import structlog

from job_ftch.application.contracts import ShotStoreClearError
from job_ftch.application.registry import register_managed_shot_backend
from job_ftch.domain.bgem3_card import build_bgem3_card
from job_ftch.infrastructure.relevance import shot_registry
from job_ftch.infrastructure.relevance.shot_anchor import (
    BgeMThreeQdrantShotStore,
    Shot,
)

if TYPE_CHECKING:
    from job_ftch.config import Settings
    from job_ftch.domain import ManagedCandidateProfile

logger = structlog.get_logger(__name__)

# BgeMThreeProvider lazily loads the (multi-GB) BGE-M3 model on first
# encode() call and keeps it in memory for the life of the instance.
# _try_get_qdrant_provider used to construct a brand-new instance on
# every call, which meant every single shot add/remove/sync reloaded
# the full model from scratch \u2014 observed in production as CPU pegged
# at 500%+, memory climbing past 10GB, and 150+ leaked model threads
# after a handful of resume uploads. Cache one instance per model name
# for the life of the process instead.
_provider_cache: dict[str, Any] = {}
_provider_cache_lock = threading.Lock()


def _qdrant_id_for(*parts: str) -> str:
    """Stable UUID for a profile-scoped category and content hash."""
    key = "\u241f".join(parts).encode()
    digest = hashlib.md5(key, usedforsecurity=False).hexdigest()
    return str(uuid.UUID(digest))


def _try_get_qdrant_provider(settings: Settings) -> Any:
    """Return the process-wide cached provider matching runtime settings, or None."""
    if not getattr(settings, "bgem3_enabled", False):
        return None
    try:
        from job_ftch.infrastructure.embeddings.bgem3 import BgeMThreeProvider

        model_name = settings.bgem3_model
        with _provider_cache_lock:
            provider = _provider_cache.get(model_name)
            if provider is None:
                provider = BgeMThreeProvider(model_name)
                _provider_cache[model_name] = provider
            return provider
    except Exception:  # noqa: BLE001
        return None


def _try_get_qdrant_store(settings: Settings) -> BgeMThreeQdrantShotStore | None:
    """Return a Qdrant shot store for the runtime settings, or None."""
    try:
        # The MVP production graph deliberately has no BGE-M3 decision path.
        # Profile editing must not override that policy by initializing the
        # heavyweight provider merely to mirror optional future-use shots.
        if not getattr(settings, "bgem3_enabled", False):
            return None
        if getattr(settings, "relevance_shot_backend", "qdrant") != "qdrant":
            return None
        provider = _try_get_qdrant_provider(settings)
        if provider is None:
            return None
        return BgeMThreeQdrantShotStore(
            url=str(settings.qdrant_url),
            api_key=(
                settings.qdrant_api_key.get_secret_value()
                if settings.qdrant_api_key is not None
                else None
            ),
            collection=settings.relevance_shot_collection_bgem3,
            provider=provider,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("qdrant_store_unavailable: %s", exc)
        return None


def _encode_for_provider(provider: Any, text: str) -> dict[str, Any]:
    return cast(
        "dict[str, Any]",
        provider.encode(
            build_bgem3_card(text, max_chars=4096), max_length=1024, return_sparse=True
        ),
    )


def _shot_from_encoded(
    *,
    role: str,
    text: str,
    label: str,
    encoded: dict[str, Any],
    provenance: dict[str, str] | None = None,
) -> Shot:
    sparse = {str(int(k)): float(v) for k, v in (encoded.get("sparse") or {}).items()}
    return Shot(
        label=label,
        role=role,
        text=text,
        vector=np.asarray(encoded["dense"], dtype=np.float32),
        sparse_weights=sparse,
        provenance=provenance or {},
    )


def _has_shot(store: Any, role: str, text: str) -> bool:
    with store._lock:
        for shot in store._shots:
            if shot.role == role and shot.text == text:
                return True
    return False


@register_managed_shot_backend("default")
def create_managed_shot_backend(settings: Settings) -> object:
    return RegistryManagedShotBackend(settings)


class RegistryManagedShotBackend:
    """Managed-shot backend that mirrors to in-memory registry and optional Qdrant."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def add_shot(
        self,
        *,
        text: str,
        label: str,
        role: str,
        tenant_id: str,
        user_id: str,
    ) -> None:
        del tenant_id, user_id
        text = (text or "").strip()
        if not text or label not in ("positive", "negative"):
            return

        store = shot_registry.get_store()
        if store is not None:
            try:
                store.add_text(text, label=label, role=role)
            except Exception as exc:  # noqa: BLE001
                logger.warning("shot_inmemory_add_failed", role=role, error=str(exc))

        qdrant = _try_get_qdrant_store(self._settings)
        if qdrant is not None:
            try:
                qdrant.ensure_collection()
                provider = getattr(qdrant, "_provider", None) or _try_get_qdrant_provider(
                    self._settings
                )
                if provider is None:
                    return
                encoded = _encode_for_provider(provider, text)
                shot = _shot_from_encoded(role=role, text=text, label=label, encoded=encoded)
                qdrant.upsert_shots_with_ids([(_qdrant_id_for(role, text), shot)])
            except ValueError as exc:
                logger.warning("qdrant_shot_dim_mismatch", role=role, error=str(exc))
            except Exception as exc:  # noqa: BLE001
                logger.warning("shot_add_failed store=%s: %s", qdrant.__class__.__name__, exc)

    def remove_shot(self, *, text: str, role: str) -> None:
        store = shot_registry.get_store()
        if store is not None:
            try:
                store.remove_text(text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("shot_inmemory_remove_failed", error=str(exc))

        qdrant = _try_get_qdrant_store(self._settings)
        if qdrant is not None:
            try:
                qdrant.ensure_collection()
                qdrant.delete_shots_by_ids([_qdrant_id_for(role, text)])
            except Exception as exc:  # noqa: BLE001
                logger.warning("qdrant_delete_failed", role=role, error=str(exc))
                # A failed delete leaves a stale vector that keeps
                # influencing relevance scoring even though the
                # candidate profile (Postgres) has already dropped the
                # text — the user has no way to notice this from the
                # UI. Surface it instead of swallowing, so callers can
                # tell the user the deletion was only partial.
                raise ShotStoreClearError(f"qdrant delete failed for role={role}") from exc

    def remove_user_shots(self, *, tenant_id: str, user_id: str) -> int:
        role_prefix = f"user:{user_id}@tenant:{tenant_id}:"
        store = shot_registry.get_store()
        removed_inmem = 0
        if store is not None:
            removed_inmem = store.clear_by_role(role_prefix)

        qdrant = _try_get_qdrant_store(self._settings)
        if qdrant is not None:
            try:
                qdrant.ensure_collection()
                qdrant.delete_by_role_prefix(role_prefix)
            except Exception as exc:  # noqa: BLE001
                logger.warning("qdrant_user_clear_failed", error=str(exc))
        return removed_inmem

    async def sync_profile_to_shot_store(
        self,
        *,
        profile: ManagedCandidateProfile,
        tenant_id: str,
        user_id: str,
    ) -> tuple[int, int]:
        store = shot_registry.get_store()
        qdrant_provider = _try_get_qdrant_provider(self._settings)
        if store is None and qdrant_provider is None:
            return 0, 0
        if not profile.profile.search_profiles:
            return 0, 0

        # Every text below goes through a synchronous, CPU-bound BGE-M3
        # encode() call. Running that inline on the event-loop thread
        # blocks all other bot activity (including Telegram polling)
        # for as long as the sync takes, which for a profile with many
        # accumulated resumes can be tens of seconds. Offload the whole
        # synchronous body to a worker thread.
        return await asyncio.to_thread(
            self._sync_profile_to_shot_store_blocking,
            profile=profile,
            tenant_id=tenant_id,
            user_id=user_id,
            store=store,
            qdrant_provider=qdrant_provider,
        )

    def _sync_profile_to_shot_store_blocking(
        self,
        *,
        profile: ManagedCandidateProfile,
        tenant_id: str,
        user_id: str,
        store: Any,
        qdrant_provider: Any,
    ) -> tuple[int, int]:
        search_profile = profile.profile.search_profiles[0]
        role_prefix = f"user:{user_id}@tenant:{tenant_id}:"

        def push(texts: tuple[str, ...], label: str, sub_role: str) -> None:
            if store is None:
                logger.warning(
                    "shot_store_unavailable: store is None, shot not synced to in-memory backend"
                )
                return
            for text in texts:
                if not text or not text.strip():
                    continue
                full_role = f"{role_prefix}{sub_role}"
                try:
                    if _has_shot(store, full_role, text):
                        continue
                except Exception:  # noqa: BLE001
                    pass
                try:
                    store.add_text(text, label=label, role=full_role)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("shot_store_add_failed", role=full_role, error=str(exc))

        push(search_profile.positive_example_texts, "positive", "resume:positive")
        push(search_profile.negative_example_texts, "negative", "resume:negative")
        push(search_profile.positive_job_example_texts, "positive", "vacancy:positive")
        push(search_profile.negative_job_example_texts, "negative", "vacancy:negative")

        qdrant = _try_get_qdrant_store(self._settings)
        if qdrant is not None:
            try:
                qdrant.ensure_collection()
                qdrant_provider = getattr(qdrant, "_provider", None) or qdrant_provider
            except Exception as exc:  # noqa: BLE001
                raise ShotStoreClearError("cannot initialize live Qdrant shot collection") from exc
        if qdrant is not None and qdrant_provider is not None:
            profile_hash = hashlib.sha256(profile.model_dump_json().encode()).hexdigest()
            created_at = datetime.now(UTC).isoformat()
            written = 0
            for texts, label, sub in (
                (search_profile.positive_example_texts, "positive", "resume:positive"),
                (search_profile.negative_example_texts, "negative", "resume:negative"),
                (search_profile.positive_job_example_texts, "positive", "vacancy:positive"),
                (search_profile.negative_job_example_texts, "negative", "vacancy:negative"),
            ):
                for text in texts:
                    if not text or not text.strip():
                        continue
                    full_role = f"{role_prefix}{sub}"
                    content_hash = hashlib.sha256(text.encode()).hexdigest()
                    try:
                        encoded = _encode_for_provider(qdrant_provider, text)
                    except Exception as exc:  # noqa: BLE001
                        raise RuntimeError(f"failed to encode profile shot category={sub}") from exc
                    try:
                        shot = _shot_from_encoded(
                            role=full_role,
                            text=text,
                            label=label,
                            encoded=encoded,
                            provenance={
                                "tenant_id": tenant_id,
                                "user_id": user_id,
                                "profile_id": profile.profile_id,
                                "profile_hash": profile_hash,
                                "category": sub,
                                "source_type": sub.split(":", 1)[0],
                                "content_hash": content_hash,
                                "embedding_model": self._settings.bgem3_model,
                                "embedding_version": "mvp_v1",
                                "created_at": created_at,
                            },
                        )
                        point_id = _qdrant_id_for(
                            tenant_id, user_id, profile.profile_id, sub, content_hash
                        )
                        qdrant.upsert_shots_with_ids([(point_id, shot)])
                        written += 1
                    except Exception as exc:  # noqa: BLE001
                        raise RuntimeError(f"failed to upsert profile shot category={sub}") from exc
            expected = sum(
                len(tuple(text for text in texts if text and text.strip()))
                for texts in (
                    search_profile.positive_example_texts,
                    search_profile.negative_example_texts,
                    search_profile.positive_job_example_texts,
                    search_profile.negative_job_example_texts,
                )
            )
            if written != expected:
                raise RuntimeError(
                    f"live Qdrant shot rebuild incomplete: wrote {written}, expected {expected}"
                )

        if store is None:
            return 0, 0
        size = store.size()
        if isinstance(size, tuple):
            return size
        return (int(size), 0)
