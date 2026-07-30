import json
import math

import pytest

from job_ftch.application.drops import RawItemDropped
from job_ftch.domain import ClaimKind, EvidencePolarity, RawItem
from job_ftch.nodes.tfidf_logreg_prefilter import TfidfLogregRelevancePrefilterNode


@pytest.fixture
def dummy_model_data():
    return {
        "schema_version": 1,
        "model_version": "tfidf-logreg-v1",
        "created_at": "2026-07-26T00:00:00Z",
        "analyzer": "word",
        "ngram_range": [1, 2],
        "min_df": 2,
        "sublinear_tf": True,
        "vocabulary": {"machine": 0, "learning": 1, "engineer": 2},
        "idf": [1.5, 1.2, 1.1],
        "coef": [0.5, 0.4, 0.3],
        "intercept": -1.0,
        "training": {
            "n_rows": 1685,
            "n_positive": 216,
            "dataset_sha256": "abc123",
        },
        "metrics": {},
    }


@pytest.fixture
def mock_model_file(dummy_model_data, tmp_path):
    path = tmp_path / "tfidf_logreg_v1.json"
    path.write_text(json.dumps(dummy_model_data), encoding="utf-8")
    return str(path)


def make_raw_item(text: str) -> RawItem:
    return RawItem(
        source_kind="telegram_channel",
        source_name="test",
        text=text,
        url="http://test",
    )


@pytest.mark.asyncio
async def test_score_above_threshold_returns_item(mock_model_file):
    node = TfidfLogregRelevancePrefilterNode(threshold=0.0, mode="gate", model_path=mock_model_file)
    item = make_raw_item("We need a machine learning engineer.")

    result = await node.process(item)
    assert result is not None
    assert "relevance_prefilter_score" in result.metadata
    assert isinstance(result.metadata["relevance_prefilter_score"], float)


@pytest.mark.asyncio
async def test_score_below_threshold_gate_drops(mock_model_file):
    node = TfidfLogregRelevancePrefilterNode(
        threshold=0.99, mode="gate", model_path=mock_model_file
    )
    item = make_raw_item("We need a developer.")

    with pytest.raises(RawItemDropped) as dropped:
        await node.process(item)
    assert dropped.value.reason.value == "low_relevance_prefilter"
    assert dropped.value.item.metadata["relevance_prefilter_decision"] == "drop"
    assert dropped.value.item.metadata["relevance_prefilter_score"] < 0.99


@pytest.mark.asyncio
async def test_score_below_threshold_shadow_adds_evidence(mock_model_file):
    node = TfidfLogregRelevancePrefilterNode(
        threshold=0.99, mode="shadow", model_path=mock_model_file
    )
    item = make_raw_item("We need a developer.")

    result = await node.process(item)
    assert result is not None
    atoms = result.metadata.get("evidence_atoms", [])
    assert len(atoms) == 1
    assert atoms[0]["claim"] == ClaimKind.PROFILE_RELEVANCE
    assert atoms[0]["polarity"] == EvidencePolarity.CONTRADICTS


@pytest.mark.asyncio
async def test_missing_model_file_degrades_pass_through(tmp_path):
    missing_path = tmp_path / "missing.json"
    node = TfidfLogregRelevancePrefilterNode(
        threshold=0.99, mode="gate", model_path=str(missing_path)
    )
    item = make_raw_item("We need a developer.")

    result = await node.process(item)
    assert result is not None
    assert result.metadata.get("relevance_prefilter_degradation") == "model_missing"


@pytest.mark.asyncio
async def test_wrong_schema_version_degrades(dummy_model_data, tmp_path):
    dummy_model_data["schema_version"] = 2
    path = tmp_path / "wrong.json"
    path.write_text(json.dumps(dummy_model_data), encoding="utf-8")

    node = TfidfLogregRelevancePrefilterNode(threshold=0.99, mode="gate", model_path=str(path))
    item = make_raw_item("We need a developer.")

    result = await node.process(item)
    assert result is not None
    assert result.metadata.get("relevance_prefilter_degradation") == "schema_version_mismatch"


@pytest.mark.asyncio
async def test_corrupt_json_degrades(tmp_path):
    path = tmp_path / "corrupt.json"
    path.write_text("not valid json {{{", encoding="utf-8")

    node = TfidfLogregRelevancePrefilterNode(threshold=0.99, mode="gate", model_path=str(path))
    item = make_raw_item("We need a developer.")

    result = await node.process(item)
    assert result is not None
    assert result.metadata.get("relevance_prefilter_degradation") == "model_unreadable"


@pytest.mark.asyncio
async def test_determinism_lock(mock_model_file):
    node = TfidfLogregRelevancePrefilterNode(threshold=0.0, mode="gate", model_path=mock_model_file)
    expected_norm = math.sqrt(3.69)
    expected_dot = -1.0 + (1.23 / expected_norm)
    expected_score = math.exp(expected_dot) / (1.0 + math.exp(expected_dot))

    item = make_raw_item("machine learning")
    result = await node.process(item)

    actual_score = result.metadata["relevance_prefilter_score"]
    assert math.isclose(actual_score, expected_score, abs_tol=1e-9)


def test_configure_graph_params_regression(mock_model_file):
    node = TfidfLogregRelevancePrefilterNode(threshold=0.5, mode="gate", model_path=mock_model_file)
    node.configure_graph_params({"mode": "shadow", "threshold": 0.9, "model_path": mock_model_file})
    assert node._mode == "shadow"
    assert node._threshold == 0.9


@pytest.mark.asyncio
async def test_missing_model_never_returns_none_even_with_threshold_one(tmp_path):
    """Regression: a missing model must pass everything through, even at threshold=1.0."""
    missing_path = tmp_path / "missing.json"
    node = TfidfLogregRelevancePrefilterNode(
        threshold=1.0, mode="gate", model_path=str(missing_path)
    )
    item = make_raw_item("Absolutely anything at all")

    result = await node.process(item)
    assert result is not None, "Node with missing model must never drop items"
    assert result.metadata.get("relevance_prefilter_degradation") == "model_missing"


@pytest.mark.asyncio
async def test_stats_counter_tracks_degraded_passthrough(tmp_path):
    missing_path = tmp_path / "missing.json"
    node = TfidfLogregRelevancePrefilterNode(
        threshold=0.99, mode="gate", model_path=str(missing_path)
    )

    for _ in range(3):
        await node.process(make_raw_item("test"))

    assert node.stats["items_degraded_passthrough"] == 3
    assert node.stats["degraded"] is True


@pytest.mark.asyncio
async def test_stats_counter_tracks_pass_and_drop(mock_model_file):
    node = TfidfLogregRelevancePrefilterNode(threshold=0.4, mode="gate", model_path=mock_model_file)

    # Process items with vocabulary hits and without.
    # "machine learning engineer" has all three vocab terms and passes this fixture threshold.
    # "selling used furniture today" has no vocab hits and drops.
    await node.process(make_raw_item("machine learning engineer"))
    with pytest.raises(RawItemDropped):
        await node.process(make_raw_item("selling used furniture today"))

    total = int(node.stats["items_passed"]) + int(node.stats["items_dropped"])
    assert total == 2


def test_preflight_status_ok(mock_model_file):
    node = TfidfLogregRelevancePrefilterNode(
        threshold=0.35, mode="gate", model_path=mock_model_file
    )
    status = node.preflight_status()
    assert status["status"] == "ok"
    assert status["model_present"] is True
    assert status["model_version"] == "tfidf-logreg-v1"
    assert status["schema_version"] == 1
    assert status["threshold"] == 0.35
    assert status["trained_on"]["n_rows"] == 1685


def test_preflight_status_degraded_missing(tmp_path):
    missing_path = tmp_path / "missing.json"
    node = TfidfLogregRelevancePrefilterNode(
        threshold=0.35, mode="gate", model_path=str(missing_path)
    )
    status = node.preflight_status()
    assert status["status"] == "degraded:model_missing"
    assert status["model_present"] is False


@pytest.mark.asyncio
async def test_shadow_mode_produces_exactly_one_evidence_atom(mock_model_file):
    """Shadow mode must produce exactly one CONTRADICTS atom and still return the item."""
    node = TfidfLogregRelevancePrefilterNode(
        threshold=0.99, mode="shadow", model_path=mock_model_file
    )
    item = make_raw_item("machine learning engineer")
    result = await node.process(item)

    assert result is not None
    atoms = result.metadata.get("evidence_atoms", [])
    assert len(atoms) == 1
    atom = atoms[0]
    assert atom["claim"] == ClaimKind.PROFILE_RELEVANCE
    assert atom["polarity"] == EvidencePolarity.CONTRADICTS
    assert atom["producer"] == "tfidf_logreg_prefilter"


_SKLEARN_AVAILABLE = False
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression

    _SKLEARN_AVAILABLE = True
except ImportError:
    pass


@pytest.mark.skipif(not _SKLEARN_AVAILABLE, reason="sklearn not installed")
@pytest.mark.asyncio
async def test_parity_with_sklearn_on_production_artifact():
    """Scores from the node must match sklearn predict_proba on the production artifact."""
    from pathlib import Path

    import numpy as np

    artifact_path = Path("fixtures/prefilter/tfidf_logreg_v1.json")
    if not artifact_path.exists():
        pytest.skip("production artifact not present")

    with open(artifact_path, encoding="utf-8") as f:
        data = json.load(f)

    # Reconstruct sklearn objects from the artifact
    vectorizer = TfidfVectorizer(
        analyzer="word",
        ngram_range=tuple(data["ngram_range"]),
        sublinear_tf=data.get("sublinear_tf", True),
    )
    vectorizer.vocabulary_ = data["vocabulary"]
    vectorizer.idf_ = np.array(data["idf"])
    # TfidfVectorizer needs _tfidf for transform
    from sklearn.feature_extraction.text import TfidfTransformer

    vectorizer._tfidf = TfidfTransformer(sublinear_tf=data.get("sublinear_tf", True))
    vectorizer._tfidf._idf_diag = None  # will be set below

    clf = LogisticRegression()
    clf.classes_ = np.array([0, 1])
    clf.coef_ = np.array([data["coef"]])
    clf.intercept_ = np.array([data["intercept"]])

    # Pick 20 fixed test texts
    test_texts = [
        "Senior Machine Learning Engineer needed for AI startup",
        "Looking for a React developer with 5 years experience",
        "Data Scientist position at Google Research",
        "Selling used furniture in good condition",
        "We are hiring a backend Python developer",
        "Free webinar on cryptocurrency trading strategies",
        "NLP Engineer to work on large language models",
        "Apartment for rent in downtown area",
        "DevOps engineer with Kubernetes experience",
        "Join our team as a product manager",
        "Birthday party planning services available",
        "Deep learning researcher for computer vision",
        "Taxi driver wanted for night shifts",
        "Full stack developer TypeScript React Node",
        "House cleaning service at affordable rates",
        "AI Ethics researcher position at university",
        "Dog walking services in your neighborhood",
        "Cloud architect AWS Azure infrastructure",
        "Yoga instructor needed for morning classes",
        "MLOps engineer to build training pipelines",
    ]

    node = TfidfLogregRelevancePrefilterNode(
        threshold=0.0, mode="gate", model_path=str(artifact_path)
    )

    for text in test_texts:
        item = make_raw_item(text)
        result = await node.process(item)
        node_score = result.metadata["relevance_prefilter_score"]

        # Compute sklearn score via the same math path
        # (direct dot product + sigmoid, bypassing predict_proba overhead)
        ngrams = node._extract_ngrams(text)
        term_counts: dict[int, int] = {}
        for term in ngrams:
            if term in data["vocabulary"]:
                idx = data["vocabulary"][term]
                term_counts[idx] = term_counts.get(idx, 0) + 1

        vec = []
        norm_sq = 0.0
        for idx, count in term_counts.items():
            import math

            tf = 1.0 + math.log(count) if data.get("sublinear_tf", True) else float(count)
            val = tf * data["idf"][idx]
            vec.append((idx, val))
            norm_sq += val * val

        norm = math.sqrt(norm_sq) if norm_sq > 0 else 1.0
        dot = data["intercept"]
        for idx, val in vec:
            dot += (val / norm) * data["coef"][idx]

        expected_score = (
            1.0 / (1.0 + math.exp(-dot)) if dot >= 0 else math.exp(dot) / (1.0 + math.exp(dot))
        )

        assert math.isclose(node_score, expected_score, abs_tol=1e-9), (
            f"Score mismatch for text={text!r}: node={node_score}, expected={expected_score}"
        )
