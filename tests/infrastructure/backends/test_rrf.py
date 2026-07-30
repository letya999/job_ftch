from job_ftch.infrastructure.backends.search.rrf import reciprocal_rank_fusion


def test_reciprocal_rank_fusion():
    list1 = ["A", "B", "C"]
    list2 = ["B", "D", "A"]

    # A: 1/61 + 1/63 = 0.03227
    # B: 1/62 + 1/61 = 0.03252
    # C: 1/63 + 0 = 0.01587
    # D: 0 + 1/62 = 0.01612
    # Order: B, A, D, C

    result = reciprocal_rank_fusion([list1, list2])
    assert result == ["B", "A", "D", "C"]


def test_rrf_deterministic_ordering():
    # Identical scores should be sorted by id for determinism

    # All get same rank (different lists of length 1)
    result = reciprocal_rank_fusion([["C"], ["B"], ["A"]])
    assert result == ["A", "B", "C"]  # alphabetical if scores are identical
