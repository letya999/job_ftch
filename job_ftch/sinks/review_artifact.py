"""Compact, human-readable projection for review diagnostics.

Implementation lives in ``outcome_artifact``; this module re-exports the
historical names used by builder and scripts.
"""

from __future__ import annotations

from job_ftch.sinks.outcome_artifact import (
    CompactReviewSink,
    compact_review_payload,
)

__all__ = ["CompactReviewSink", "compact_review_payload"]
