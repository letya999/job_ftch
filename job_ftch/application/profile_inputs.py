"""Profile-input orchestrator (re-exports + glue).

This module is the public entry point for profile-input helpers. It
re-exports the building blocks from the three sub-modules and exposes
the three glue-level helpers (`embed_profile_examples`,
`remove_example_from_profile`, `list_examples`) that operate on
`ManagedCandidateProfile` and are used by the bot/MCP adapter API.

Split (per the v0.0.4 MVP cleanup):

  - `profile_parsing.py`        — pure-data helpers: CSV merging, language
                                  normalisation, `ResumeExtractionPayload`,
                                  heuristic resume payload, build profile
                                  from a simple dict payload.
  - `resume_extraction.py`     — LLM-aware resume extraction
                                  (`_extract_resume_payload`,
                                  `build_profile_from_resume_text_async`,
                                  `merge_resume_profile`,
                                  `add_example_to_profile`).
  - `ontology_enrichment.py`    — ontology shot enrichment
                                  (`_build_shot_extraction_prompt`,
                                  `_enrich_ontology_from_shot`,
                                  `add_example_to_profile_with_enrichment`,
                                  `load_resume_with_enrichment`).
  - `profile_inputs.py`         — this file: re-exports + 3 glue helpers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from job_ftch.application.ontology_enrichment import (
    add_example_to_profile_with_enrichment,
    load_resume_with_enrichment,
)
from job_ftch.application.profile_parsing import (
    ResumeExtractionPayload,
    build_candidate_profile_from_payload,
    build_profile_catalog,
)
from job_ftch.application.resume_extraction import (
    add_example_to_profile,
    build_profile_from_resume_text,
    build_profile_from_resume_text_async,
    merge_resume_profile,
)

if TYPE_CHECKING:
    from job_ftch.domain import ManagedCandidateProfile, SearchProfile


logger = structlog.get_logger(__name__)


__all__ = [
    "ResumeExtractionPayload",
    "add_example_to_profile",
    "add_example_to_profile_with_enrichment",
    "build_candidate_profile_from_payload",
    "build_profile_catalog",
    "build_profile_from_resume_text",
    "build_profile_from_resume_text_async",
    "embed_profile_examples",
    "list_examples",
    "load_resume_with_enrichment",
    "merge_resume_profile",
    "remove_example_from_profile",
]


async def embed_search_profile(
    profile: SearchProfile,
    embedding_provider: Any,
) -> SearchProfile:
    """Embed all shots from a SearchProfile into its embedding_vector.

    Combines resume + vacancy shots into a single mean embedding.
    BR-4: called on every pipeline run to refresh embedding_vector.
    """
    import numpy as np

    all_pos = list(profile.positive_example_texts) + list(profile.positive_job_example_texts)
    all_neg = list(profile.negative_example_texts) + list(profile.negative_job_example_texts)

    pos_vecs = await embedding_provider.embed(all_pos) if all_pos else []
    neg_vecs = await embedding_provider.embed(all_neg) if all_neg else []

    embedding_vector = None
    if pos_vecs:
        mean_arr = np.mean(np.array(pos_vecs, dtype=np.float32), axis=0)
        embedding_vector = tuple(float(x) for x in mean_arr)

    negative_embedding_vectors: tuple[tuple[float, ...], ...] = ()
    if neg_vecs:
        negative_embedding_vectors = tuple(
            tuple(float(x) for x in np.asarray(v, dtype=np.float32)) for v in neg_vecs
        )

    return profile.model_copy(
        update={
            "embedding_vector": embedding_vector,
            "negative_embedding_vectors": negative_embedding_vectors,
        }
    )


async def embed_profile_examples(
    managed: ManagedCandidateProfile,
    embedding_provider: Any,
) -> ManagedCandidateProfile:
    import numpy as np

    if not managed.profile.search_profiles:
        return managed
    sp = managed.profile.search_profiles[0]

    # Combine resume shots + vacancy shots for richer profile vector.
    # All positive examples (resume + job) contribute to the mean embedding.
    # All negative examples (resume + job) contribute to the negative vectors.
    all_pos = list(sp.positive_example_texts) + list(sp.positive_job_example_texts)
    all_neg = list(sp.negative_example_texts) + list(sp.negative_job_example_texts)

    pos_vecs = await embedding_provider.embed(all_pos) if all_pos else []
    neg_vecs = await embedding_provider.embed(all_neg) if all_neg else []

    embedding_vector = None
    if pos_vecs:
        mean_arr = np.mean(np.array(pos_vecs, dtype=np.float32), axis=0)
        embedding_vector = tuple(float(x) for x in mean_arr)

    negative_embedding_vectors: tuple[tuple[float, ...], ...] = ()
    if neg_vecs:
        negative_embedding_vectors = tuple(
            tuple(float(x) for x in np.asarray(v, dtype=np.float32)) for v in neg_vecs
        )

    updated_sp = sp.model_copy(
        update={
            "embedding_vector": embedding_vector,
            "negative_embedding_vectors": negative_embedding_vectors,
        }
    )
    updated_profile = managed.profile.model_copy(
        update={"search_profiles": (updated_sp,) + managed.profile.search_profiles[1:]}
    )
    return managed.model_copy(
        update={
            "profile": updated_profile,
            "updated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        }
    )


def remove_example_from_profile(
    managed: ManagedCandidateProfile,
    kind: str,
    index: int,
) -> ManagedCandidateProfile:
    """Remove an example text from the first search profile by type and
    index. Out-of-range index returns the profile unchanged."""
    from datetime import UTC, datetime

    from job_ftch.domain import ManagedCandidateProfile

    if not managed.profile.search_profiles:
        return managed
    sp = managed.profile.search_profiles[0]

    if kind == "positive_job":
        texts = sp.positive_job_example_texts
    elif kind == "negative_job":
        texts = sp.negative_job_example_texts
    elif kind.startswith("negative"):
        texts = sp.negative_example_texts
    else:
        texts = sp.positive_example_texts

    if index < 0 or index >= len(texts):
        return managed

    new_texts = texts[:index] + texts[index + 1 :]

    if kind == "positive_job":
        updated_sp = sp.model_copy(update={"positive_job_example_texts": new_texts})
    elif kind == "negative_job":
        updated_sp = sp.model_copy(update={"negative_job_example_texts": new_texts})
    elif kind.startswith("negative"):
        updated_sp = sp.model_copy(update={"negative_example_texts": new_texts})
    else:
        updated_sp = sp.model_copy(update={"positive_example_texts": new_texts})

    updated_profiles = (updated_sp,) + managed.profile.search_profiles[1:]
    updated_profile = managed.profile.model_copy(update={"search_profiles": updated_profiles})
    return ManagedCandidateProfile(
        user_id=managed.user_id,
        profile_id=managed.profile_id,
        profile=updated_profile,
        updated_at=datetime.now(UTC),
    )


def list_examples(managed: ManagedCandidateProfile) -> dict[str, list[str]]:
    """Return all example texts grouped by kind from the first search
    profile. (job-level examples are surfaced through a separate API.)"""
    if not managed.profile.search_profiles:
        return {
            "positive_resume": [],
            "negative_resume": [],
            "positive_job": [],
            "negative_job": [],
        }
    sp = managed.profile.search_profiles[0]
    return {
        "positive_resume": list(sp.positive_example_texts),
        "negative_resume": list(sp.negative_example_texts),
        "positive_job": list(sp.positive_job_example_texts),
        "negative_job": list(sp.negative_job_example_texts),
    }
