"""Reciprocal Rank Fusion."""

from __future__ import annotations


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    k: int = 60,
) -> list[str]:
    """
    Combine multiple ranked lists into a single ranked list using Reciprocal Rank Fusion.
    
    Score = sum(1 / (k + rank)) for each rank in each list.
    rank is 1-indexed.
    """
    scores: dict[str, float] = {}
    
    for ranked_list in ranked_lists:
        for i, item in enumerate(ranked_list):
            rank = i + 1
            score = 1.0 / (k + rank)
            scores[item] = scores.get(item, 0.0) + score
            
    # Sort by score descending. For stable sort, use the negative score and item id.
    # Python's sort is stable, but dict iteration might not be across python versions if items are added in different orders.
    # We sort by (-score, item) to ensure deterministic order if scores are identical.
    sorted_items = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
    return [item for item, _ in sorted_items]
