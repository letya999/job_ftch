"""Small typed payloads used by legacy pipeline experiments."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RelevanceCard(BaseModel):
    """Bounded context passed to relevance decisions before full extraction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str | None = None
    employer: str | None = None
    seniority: str | None = None
    role_anchors: tuple[str, ...] = ()
    location: str | None = None
    salary_present: bool = False
    text: str = Field(default="", max_length=2000)
    evidence_spans: tuple[str, ...] = ()
