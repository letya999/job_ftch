"""ShotExtraction domain model — structured output of LLM point ①.

Used by ``add_example_to_profile_with_enrichment`` and ``load_resume_with_enrichment``
to update the live ontology on-the-fly when a positive/negative shot is loaded.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RelevanceKeyword(BaseModel):
    """Weighted positive/negative relevance cue extracted from a shot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    term: str
    weight: int = Field(default=1, ge=1, le=5)


class ShotExtraction(BaseModel):
    """LLM-extracted structured info from a single shot (resume or job).

    Canonical names of skills/roles/technologies MUST be in English lowercase
    regardless of input language. The ``anti_patterns`` field holds text in the
    language of the input shot (per ADR-019 language rule).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    skills: tuple[str, ...] = ()
    """Canonical English-lowercase skill names (e.g. 'python', 'pytorch')."""

    roles: tuple[str, ...] = ()
    """Canonical English-lowercase role names (e.g. 'data scientist')."""

    seniority: tuple[str, ...] = ()
    """From: intern, junior, middle, senior, lead, principal, head."""

    anti_patterns: tuple[str, ...] = ()
    """Free-form phrases in the language of the input shot, describing
    what to AVOID (for negative shots)."""

    positive_keywords: tuple[RelevanceKeyword, ...] = ()
    """Weighted positive relevance cues for the active profile."""

    negative_keywords: tuple[RelevanceKeyword, ...] = ()
    """Weighted negative relevance cues for the active profile."""
