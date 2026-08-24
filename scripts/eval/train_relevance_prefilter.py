#!/usr/bin/env python3
"""Train the TF-IDF + logistic regression relevance prefilter and export to JSON.

The model learns entirely from the labelled dataset with zero domain words in
code. A different vacancy domain needs a different dataset and its own trained
artifact - the code is domain-agnostic.

Known defect fixed in this version: the threshold sweep and positive-retention
guard now use a stratified held-out split instead of training data. The held-out
fraction defaults to 20% and can be overridden with --holdout-fraction.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from job_ftch.application.prefilter_artifacts import (
    DEFAULT_HOLDOUT_FRACTION,
    DEFAULT_THRESHOLD,
    dataset_stats,
    load_dataset_rows,
    sklearn_status,
    train_tfidf_logreg,
)


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
        default=DEFAULT_THRESHOLD,
        help="Threshold for positive retention check",
    )
    parser.add_argument(
        "--holdout-fraction",
        type=float,
        default=DEFAULT_HOLDOUT_FRACTION,
        help="Fraction of data held out for the retention sweep (default 0.20)",
    )
    args = parser.parse_args()

    excluded_ids: set[str] = set()
    if args.exclude_ids and args.exclude_ids.exists():
        with open(args.exclude_ids, encoding="utf-8") as handle:
            excluded_ids = {line.strip() for line in handle if line.strip()}

    rows = [row for row in load_dataset_rows(args.dataset) if row["stable_id"] not in excluded_ids]
    stats = dataset_stats(rows)
    print(
        f"Loaded {stats['n_rows']} samples "
        f"({stats['n_positive']} positive, {stats['positive_fraction']:.3f} fraction)."
    )
    print(f"Excluded {len(excluded_ids)} IDs.")
    if not stats["ok"]:
        for error in stats["errors"]:
            print(f"ERROR: {error}", file=sys.stderr)
        print("Artifact NOT written.", file=sys.stderr)
        raise SystemExit(1)

    status = sklearn_status()
    if not status["present"]:
        print("scikit-learn is required. run: pip install scikit-learn", file=sys.stderr)
        raise SystemExit(1)

    print(
        f"Split uses holdout_fraction={args.holdout_fraction:.0%} and threshold={args.threshold}."
    )
    print("Training TF-IDF + LogisticRegression...")
    trained = train_tfidf_logreg(
        texts=[row["text"] for row in rows],
        labels=[int(row["relevant"]) for row in rows],
        dataset_path=args.dataset,
        threshold=args.threshold,
        holdout_fraction=args.holdout_fraction,
    )
    if not trained.get("ok"):
        print(f"ERROR: {trained.get('message')}", file=sys.stderr)
        print("Artifact NOT written.", file=sys.stderr)
        raise SystemExit(1)

    artifact = trained["artifact"]
    artifact["training"]["excluded_ids"] = len(excluded_ids)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    metrics = artifact["metrics"]
    print(f"Exported to {args.out}")
    print(
        f"Full training size: {artifact['training']['n_rows']} rows "
        f"({artifact['training']['n_positive']} positive)"
    )
    print(
        f"Holdout retention at threshold {args.threshold}: "
        f"{metrics['holdout_positive_retention']:.4f}"
    )


if __name__ == "__main__":
    main()
