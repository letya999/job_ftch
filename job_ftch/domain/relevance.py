"""RelevanceClassification domain model — structured output of LLM point ②.

Used by ``LLMRelevanceClassificationNode`` to decide borderline cases where
``MultiProfileMatchNode`` similarity is in (low_threshold, high_threshold).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class RelevanceClassification(BaseModel):
    """LLM-decided relevance for a borderline job posting.

    Free-form text fields (``reasoning``, ``matched_positive_aspects``,
    ``mismatched_aspects``) are in the language of the input job text.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["accept", "reject", "review"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    matched_positive_aspects: tuple[str, ...] = ()
    mismatched_aspects: tuple[str, ...] = ()


EvidenceSnippetId = Annotated[int, Field(ge=1, le=16)]


class RelevanceEvidenceClassification(BaseModel):
    """Compact responsibility-first evidence returned by the LLM judge.

    The model reports observable relations and snippet references. It does not
    own the terminal decision and does not self-report a probability.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    is_job: Literal["yes", "no", "unknown"]
    role_relation: Literal["target", "adjacent", "other", "unknown"]
    responsibility_fit: Literal["support", "contradict", "unknown"]
    positive_evidence_ids: tuple[EvidenceSnippetId, ...] = Field(default=(), max_length=3)
    negative_evidence_ids: tuple[EvidenceSnippetId, ...] = Field(default=(), max_length=3)
