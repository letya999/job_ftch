import numpy as np

from job_ftch.infrastructure.relevance.shot_anchor import RelevanceScore, ShotRelevanceScorer


def unit(v):
    return v / np.linalg.norm(v)


class MockEmbedder:
    def __init__(self, vec):
        self._vec = vec

    def encode_query(self, text):
        return self._vec


def make_scorer(positives, negatives, embedder=None, top_k=3):
    pos = np.array(positives, dtype=np.float32)
    neg = (
        np.array(negatives, dtype=np.float32)
        if negatives
        else np.zeros(
            (0, positives[0].shape[0] if hasattr(positives[0], "shape") else len(positives[0])),
            dtype=np.float32,
        )
    )
    if embedder is None:
        embedder = MockEmbedder(np.ones(len(positives[0]), dtype=np.float32))
    return ShotRelevanceScorer(pos, neg, embedder, top_k=top_k)


dim = 4
rng = np.random.default_rng(0)


def test_score_vector_positive_above_negative():
    query = unit(np.array([1, 0, 0, 0], dtype=np.float32))
    pos = [unit(np.array([1, 0.1, 0, 0], dtype=np.float32))]
    neg = [unit(np.array([0, 1, 0, 0], dtype=np.float32))]
    scorer = make_scorer(pos, neg, MockEmbedder(query))
    score = scorer.score_vector(query)
    assert score.sim_pos > score.sim_neg


def test_score_vector_empty_negatives():
    query = unit(rng.standard_normal(dim).astype(np.float32))
    pos = [unit(rng.standard_normal(dim).astype(np.float32))]
    scorer = make_scorer(pos, [])
    score = scorer.score_vector(query)
    assert score.sim_neg == 0.0
    assert score.max_neg == 0.0


def test_score_vector_empty_positives():
    query = unit(rng.standard_normal(dim).astype(np.float32))
    neg = [unit(rng.standard_normal(dim).astype(np.float32))]
    scorer = ShotRelevanceScorer(
        np.zeros((0, dim), dtype=np.float32),
        np.array(neg, dtype=np.float32),
        MockEmbedder(query),
    )
    score = scorer.score_vector(query)
    assert score.sim_pos == 0.0


def test_margin_property():
    score = RelevanceScore(sim_pos=0.8, sim_neg=0.3, max_pos=0.9, max_neg=0.4)
    assert abs(score.margin - 0.5) < 1e-6


def test_score_text_uses_embedder():
    query = unit(np.array([1, 0, 0, 0], dtype=np.float32))
    pos = [unit(np.array([1, 0, 0, 0], dtype=np.float32))]
    embedder = MockEmbedder(query)
    scorer = make_scorer(pos, [], embedder)
    score = scorer.score_text("any text")
    assert score.sim_pos > 0.0


def test_topk_mean_with_more_than_k():
    query = unit(np.array([1, 0, 0, 0], dtype=np.float32))
    pos = [
        unit(np.array([1, 0, 0, 0], dtype=np.float32)),  # sim=1.0
        unit(np.array([0.9, 0.1, 0, 0], dtype=np.float32)),
        unit(np.array([0.5, 0.5, 0, 0], dtype=np.float32)),
        unit(np.array([0, 1, 0, 0], dtype=np.float32)),  # sim=0.0
    ]
    scorer = make_scorer(pos, [], MockEmbedder(query), top_k=2)
    score = scorer.score_vector(query)
    # top-2 mean should be higher than overall mean
    all_sims = np.array([1.0, 0.9 / np.sqrt(0.82), 0.5 / np.sqrt(0.5), 0.0])
    assert score.sim_pos > float(all_sims.mean())
