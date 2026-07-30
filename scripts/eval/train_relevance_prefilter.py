#!/usr/bin/env python3
"""Train the TF-IDF + logistic regression relevance prefilter and export to JSON.

The model learns entirely from the labelled dataset with zero domain words in
code. A different vacancy domain needs a different dataset and its own trained
artifact - the code is domain-agnostic.

Known defect fixed in this version: the threshold sweep and positive-retention
guard now use a stratified held-out split instead of training data. The held-out
fraction defaults to 20% and can be overridden with --holdout-fraction.
"""

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

try:
    import sklearn
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedShuffleSplit
except ImportError:
    print("scikit-learn is required. run: pip install scikit-learn", file=sys.stderr)
    sys.exit(1)

# Minimum dataset requirements. Derived from our working case (1685 rows,
# 216 positives gave retention 0.96 at threshold 0.30) with headroom so a
# new user's first attempt does not silently produce a bad model.
MIN_ROWS = 2000
MIN_POSITIVES = 150
MIN_POSITIVE_FRACTION = 0.02
MAX_POSITIVE_FRACTION = 0.50


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train TF-IDF + logistic regression relevance prefilter"
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("fixtures/dataset/eval_dataset.jsonl"),
        help="Input JSONL with fields: stable_id, text, relevant (0 or 1)",
    )
    parser.add_argument(
        "--exclude-ids",
        type=Path,
        help="File with stable_ids to exclude (one per line)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("fixtures/prefilter/tfidf_logreg_v1.json"),
        help="Output JSON artifact path",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.30,
        help="Threshold for positive retention check",
    )
    parser.add_argument(
        "--holdout-fraction",
        type=float,
        default=0.20,
        help="Fraction of data held out for the retention sweep (default 0.20)",
    )
    args = parser.parse_args()

    excluded_ids: set[str] = set()
    if args.exclude_ids and args.exclude_ids.exists():
        with open(args.exclude_ids, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    excluded_ids.add(line.strip())

    texts: list[str] = []
    labels: list[int] = []

    with open(args.dataset, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("stable_id") in excluded_ids:
                continue

            relevant = record.get("relevant")
            if type(relevant) is int and relevant in (0, 1):
                texts.append(record["text"])
                labels.append(relevant)

    n_rows = len(texts)
    n_positive = sum(labels)
    pos_fraction = n_positive / n_rows if n_rows > 0 else 0.0

    print(f"Loaded {n_rows} samples ({n_positive} positive, {pos_fraction:.3f} fraction).")
    print(f"Excluded {len(excluded_ids)} IDs.")

    # Dataset minimum checks
    errors: list[str] = []
    if n_rows < MIN_ROWS:
        errors.append(f"Dataset has {n_rows} rows, minimum is {MIN_ROWS}.")
    if n_positive < MIN_POSITIVES:
        errors.append(f"Dataset has {n_positive} positives, minimum is {MIN_POSITIVES}.")
    if n_rows > 0 and not (MIN_POSITIVE_FRACTION <= pos_fraction <= MAX_POSITIVE_FRACTION):
        errors.append(
            f"Positive fraction {pos_fraction:.3f} is outside allowed range "
            f"[{MIN_POSITIVE_FRACTION}, {MAX_POSITIVE_FRACTION}]."
        )
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        print("Artifact NOT written.", file=sys.stderr)
        sys.exit(1)

    # Stratified held-out split for threshold sweep and retention check
    holdout_fraction = args.holdout_fraction
    sss = StratifiedShuffleSplit(n_splits=1, test_size=holdout_fraction, random_state=42)
    labels_arr = np.array(labels)
    train_idx, holdout_idx = next(sss.split(texts, labels_arr))

    train_texts = [texts[i] for i in train_idx]
    train_labels = [labels[i] for i in train_idx]
    holdout_texts = [texts[i] for i in holdout_idx]
    holdout_labels_arr = labels_arr[holdout_idx]

    print(
        f"Split: {len(train_texts)} train, {len(holdout_texts)} holdout ({holdout_fraction:.0%})."
    )
    print(f"Holdout positives: {int(holdout_labels_arr.sum())} / {len(holdout_texts)}.")

    # Train on the training split first to compute held-out metrics
    print("Training TF-IDF + LogisticRegression on train split...")
    vectorizer = TfidfVectorizer(
        analyzer="word",
        ngram_range=(1, 2),
        min_df=2,
        max_features=200000,
        sublinear_tf=True,
    )
    X_train = vectorizer.fit_transform(train_texts)
    clf = LogisticRegression(max_iter=2000, class_weight="balanced", C=4.0)
    clf.fit(X_train, train_labels)

    # Compute threshold sweep on held-out data
    X_holdout = vectorizer.transform(holdout_texts)
    holdout_probs = clf.predict_proba(X_holdout)[:, 1]

    sweep = []
    target_retention = 0.0
    n_holdout_pos = int(holdout_labels_arr.sum())
    for th in [0.1, 0.2, 0.3, 0.35, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]:
        kept = int(np.sum(holdout_probs >= th))
        kept_positives = int(np.sum((holdout_probs >= th) & (holdout_labels_arr == 1)))
        pos_retention = float(kept_positives / n_holdout_pos) if n_holdout_pos > 0 else 0.0
        sweep.append(
            {
                "threshold": float(th),
                "kept": kept,
                "positive_retention": round(pos_retention, 4),
            }
        )
        if abs(th - args.threshold) < 1e-6:
            target_retention = pos_retention

    print("Threshold sweep (held-out):")
    for row in sweep:
        print(
            f"  th={row['threshold']:.2f} -> kept={row['kept']}, "
            f"pos_retention={row['positive_retention']:.4f}"
        )

    if target_retention < 0.90:
        print(
            f"ERROR: Positive retention at threshold {args.threshold} is "
            f"{target_retention:.4f} < 0.90 on held-out data.",
            file=sys.stderr,
        )
        print("Artifact NOT written.", file=sys.stderr)
        sys.exit(1)

    # Guard passed - refit on ALL data for the final artifact
    print("Retention check passed. Refitting on all data...")
    vectorizer_full = TfidfVectorizer(
        analyzer="word",
        ngram_range=(1, 2),
        min_df=2,
        max_features=200000,
        sublinear_tf=True,
    )
    X_full = vectorizer_full.fit_transform(texts)
    clf_full = LogisticRegression(max_iter=2000, class_weight="balanced", C=4.0)
    clf_full.fit(X_full, labels)

    print("Exporting model to JSON...")

    vocab = {k: int(v) for k, v in vectorizer_full.vocabulary_.items()}

    output_data = {
        "schema_version": 1,
        "model_version": "tfidf-logreg-v1",
        "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "analyzer": "word",
        "ngram_range": [1, 2],
        "min_df": 2,
        "sublinear_tf": True,
        "vocabulary": vocab,
        "idf": vectorizer_full.idf_.tolist(),
        "coef": clf_full.coef_[0].tolist(),
        "intercept": float(clf_full.intercept_[0]),
        "training": {
            "dataset": str(args.dataset),
            "dataset_sha256": file_sha256(args.dataset),
            "n_rows": len(texts),
            "n_positive": sum(labels),
            "excluded_ids": len(excluded_ids),
            "sklearn_version": sklearn.__version__,
            "holdout_fraction": holdout_fraction,
            "holdout_size": len(holdout_texts),
            "holdout_positives": int(holdout_labels_arr.sum()),
        },
        "metrics": {
            "threshold_sweep_holdout": sweep,
            "target_threshold": args.threshold,
            "holdout_positive_retention": round(target_retention, 4),
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False)

    print(f"Exported to {args.out}")
    print(f"Full training size: {len(texts)} rows ({sum(labels)} positive)")
    print(f"Holdout retention at threshold {args.threshold}: {target_retention:.4f}")


if __name__ == "__main__":
    main()
