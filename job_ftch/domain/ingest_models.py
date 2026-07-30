"""Domain models for the three-phase career-site ingestion lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_ftch.domain.site_models import DiscoveredPostingPayload


class IngestItemStatus(StrEnum):
    """Lifecycle of a discovered career-site item.

    The shipped in-run two-phase flow only reaches ``DISCOVERED`` and ``NEW``;
    the remaining states are the target vocabulary for the durable, crash-safe
    lifecycle tracked under TD-015 (Variant B) and are not yet assigned.
    """

    DISCOVERED = "discovered"
    PROCESSING = "processing"
    NEW = "new"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    FAILED = "failed"
    EXPIRED = "expired"


CONFIDENT_THRESHOLD = 0.8
PARTIAL_THRESHOLD = 0.4


@dataclass(slots=True)
class DiscoveredCandidate:
    """A single job discovered by a monitor, before detail-page enrichment.

    ``rich_payload`` is set when the monitor (an ATS API) already returned full
    fields; ``None`` means the entry is a listing link that still needs a
    detail-page fetch in the enrich phase.
    """

    url: str
    rich_payload: DiscoveredPostingPayload | None = None
    completeness: float = 0.0
    status: IngestItemStatus = IngestItemStatus.DISCOVERED


def score_completeness(payload: DiscoveredPostingPayload) -> float:
    score = 0.1  # url is always present
    if payload.title:
        score += 0.2
    if payload.description and len(payload.description) > 100:
        score += 0.3
    if payload.metadata and payload.metadata.get("company"):
        score += 0.15
    if payload.date_posted:
        score += 0.1
    if payload.locations:
        score += 0.1
    if payload.base_salary:
        score += 0.05
    return min(score, 1.0)
