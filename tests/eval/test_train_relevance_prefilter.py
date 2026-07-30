"""Tests for the relevance prefilter training script."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path("scripts/eval/train_relevance_prefilter.py")
PYTHON = sys.executable

_SKLEARN_AVAILABLE = False
try:
    import sklearn  # noqa: F401

    _SKLEARN_AVAILABLE = True
except ImportError:
    pass


def _make_dataset(tmp_path: Path, n_rows: int, n_positive: int) -> Path:
    """Generate a synthetic JSONL dataset with the given size and label balance."""
    path = tmp_path / "synthetic.jsonl"
    rows = []
    for i in range(n_rows):
        label = 1 if i < n_positive else 0
        # Positives get ML-related words, negatives get unrelated words.
        # Enough variation to avoid min_df filtering everything out.
        if label == 1:
            text = (
                f"Machine learning engineer position number {i} "
                f"deep learning neural network NLP computer vision "
                f"Python TensorFlow PyTorch data science AI research "
                f"senior developer infrastructure cloud {i % 50}"
            )
        else:
            text = (
                f"Apartment for rent cleaning service taxi driver {i} "
                f"furniture sale birthday party yoga instructor "
                f"dog walking house cleaning plumber electrician "
                f"delivery courier restaurant waiter {i % 50}"
            )
        rows.append(
            json.dumps(
                {"stable_id": f"synth_{i}", "text": text, "relevant": label},
                ensure_ascii=False,
            )
        )
    path.write_text("\n".join(rows), encoding="utf-8")
    return path


@pytest.mark.skipif(not _SKLEARN_AVAILABLE, reason="sklearn not installed")
def test_valid_dataset_produces_artifact(tmp_path: Path) -> None:
    dataset = _make_dataset(tmp_path, n_rows=2200, n_positive=250)
    out = tmp_path / "model.json"

    result = subprocess.run(
        [PYTHON, str(SCRIPT), "--dataset", str(dataset), "--out", str(out)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert out.exists(), "Artifact was not written"

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["model_version"] == "tfidf-logreg-v1"
    assert "vocabulary" in data
    assert "idf" in data
    assert "coef" in data
    assert "intercept" in data
    # Held-out metrics recorded
    assert "holdout_fraction" in data["training"]
    assert "holdout_size" in data["training"]
    assert data["training"]["n_rows"] == 2200
    assert data["training"]["n_positive"] == 250


@pytest.mark.skipif(not _SKLEARN_AVAILABLE, reason="sklearn not installed")
def test_too_few_rows_exits_nonzero(tmp_path: Path) -> None:
    dataset = _make_dataset(tmp_path, n_rows=500, n_positive=60)
    out = tmp_path / "model.json"

    result = subprocess.run(
        [PYTHON, str(SCRIPT), "--dataset", str(dataset), "--out", str(out)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert result.returncode != 0, "Script should fail for too few rows"
    assert not out.exists(), "Artifact should not be written on failure"


@pytest.mark.skipif(not _SKLEARN_AVAILABLE, reason="sklearn not installed")
def test_too_few_positives_exits_nonzero(tmp_path: Path) -> None:
    dataset = _make_dataset(tmp_path, n_rows=2200, n_positive=50)
    out = tmp_path / "model.json"

    result = subprocess.run(
        [PYTHON, str(SCRIPT), "--dataset", str(dataset), "--out", str(out)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert result.returncode != 0, "Script should fail for too few positives"
    assert not out.exists(), "Artifact should not be written on failure"


@pytest.mark.skipif(not _SKLEARN_AVAILABLE, reason="sklearn not installed")
def test_low_retention_on_holdout_exits_nonzero(tmp_path: Path) -> None:
    """When the model cannot retain 90% of positives on held-out data, it should fail.

    We force this by setting a very high threshold that the model cannot meet.
    """
    dataset = _make_dataset(tmp_path, n_rows=2200, n_positive=250)
    out = tmp_path / "model.json"

    result = subprocess.run(
        [
            PYTHON,
            str(SCRIPT),
            "--dataset",
            str(dataset),
            "--out",
            str(out),
            "--threshold",
            "0.999",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert result.returncode != 0, "Script should fail when retention < 0.90"
    assert not out.exists(), "Artifact should not be written on failure"
