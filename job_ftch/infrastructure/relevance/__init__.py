"""Shot-anchored relevance scoring backed by a vector DB.

The active search profile is defined by *example postings* (positive and
negative shots) stored in the vector database - not by keyword lists in YAML.
Relevance of a new posting is its embedding similarity to those shots.
"""

from job_ftch.infrastructure.relevance.shot_anchor import (
    ShotEmbedder,
    ShotRelevanceScorer,
    ShotStore,
)

__all__ = ["ShotEmbedder", "ShotRelevanceScorer", "ShotStore"]
