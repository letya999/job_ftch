"""Fit a deterministic offline logistic policy from typed evidence features.

The input is JSONL.  Every row must provide ``features`` (numeric mapping),
the requested binary target, and optional ``split``/``group`` metadata.  The
script never calls an LLM and writes a versioned YAML policy artifact.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import yaml


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp = math.exp(value)
    return exp / (1.0 + exp)


def _rows(path: Path, target: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict) or value.get(target) not in (0, 1):
            continue
        features = value.get("features")
        if not isinstance(features, dict):
            raise ValueError(f"{path}:{line_no}: features must be a mapping")
        rows.append(value)
    if not rows:
        raise ValueError(f"no labelled {target} rows in {path}")
    return rows


def _feature_names(rows: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            str(name)
            for row in rows
            for name, value in row["features"].items()
            if isinstance(value, (int, float))
            and not isinstance(value, bool)
            and not str(name).endswith("final_score")
        }
    )


def _fit(rows: list[dict[str, Any]], names: list[str], target: str) -> tuple[float, list[float]]:
    weights = [0.0] * len(names)
    intercept = 0.0
    rate = 0.15
    l2 = 0.01
    for _ in range(800):
        grad_b = 0.0
        grad_w = [0.0] * len(names)
        for row in rows:
            values = [float(row["features"].get(name, 0.0)) for name in names]
            prediction = _sigmoid(
                intercept
                + sum(weight * value for weight, value in zip(weights, values, strict=True))
            )
            error = prediction - int(row[target])
            grad_b += error
            for index, value in enumerate(values):
                grad_w[index] += error * value
        scale = 1.0 / len(rows)
        intercept -= rate * grad_b * scale
        for index in range(len(weights)):
            weights[index] -= rate * (grad_w[index] * scale + l2 * weights[index])
        rate *= 0.997
    return intercept, weights


def _threshold(
    rows: list[dict[str, Any]],
    names: list[str],
    target: str,
    intercept: float,
    weights: list[float],
    precision_floor: float,
) -> tuple[float, dict[str, float]]:
    scored = []
    for row in rows:
        score = _sigmoid(
            intercept
            + sum(
                weight * float(row["features"].get(name, 0.0))
                for weight, name in zip(weights, names, strict=True)
            )
        )
        scored.append((score, int(row[target])))
    candidates = sorted({score for score, _ in scored}, reverse=True)
    selected = 1.0
    metrics = {"precision": 0.0, "recall": 0.0}
    positives = sum(label for _, label in scored)
    for candidate in candidates:
        accepted = [label for score, label in scored if score >= candidate]
        precision = sum(accepted) / len(accepted) if accepted else 0.0
        recall = sum(accepted) / positives if positives else 0.0
        if precision >= precision_floor and recall >= metrics["recall"]:
            selected, metrics = candidate, {"precision": precision, "recall": recall}
    return round(selected, 8), {name: round(value, 8) for name, value in metrics.items()}


def _correlations(rows: list[dict[str, Any]], names: list[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    for index, left in enumerate(names):
        left_values = [float(row["features"].get(left, 0.0)) for row in rows]
        left_mean = sum(left_values) / len(left_values)
        left_var = sum((value - left_mean) ** 2 for value in left_values)
        for right in names[index + 1 :]:
            right_values = [float(row["features"].get(right, 0.0)) for row in rows]
            right_mean = sum(right_values) / len(right_values)
            right_var = sum((value - right_mean) ** 2 for value in right_values)
            denominator = math.sqrt(left_var * right_var)
            if denominator:
                result[f"{left}::{right}"] = round(
                    sum(
                        (a - left_mean) * (b - right_mean)
                        for a, b in zip(left_values, right_values, strict=True)
                    )
                    / denominator,
                    8,
                )
    return result


def _ranks(values: list[float]) -> list[float]:
    """Average tied ranks without pulling a training dependency into runtime."""
    ranked = [0.0] * len(values)
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        rank = (index + 1 + end) / 2
        for original, _ in ordered[index:end]:
            ranked[original] = rank
        index = end
    return ranked


def _pearson(left_values: list[float], right_values: list[float]) -> float | None:
    left_mean = sum(left_values) / len(left_values)
    right_mean = sum(right_values) / len(right_values)
    left_var = sum((value - left_mean) ** 2 for value in left_values)
    right_var = sum((value - right_mean) ** 2 for value in right_values)
    denominator = math.sqrt(left_var * right_var)
    if not denominator:
        return None
    return (
        sum(
            (a - left_mean) * (b - right_mean)
            for a, b in zip(left_values, right_values, strict=True)
        )
        / denominator
    )


def _spearman_correlations(rows: list[dict[str, Any]], names: list[str]) -> dict[str, float]:
    values = {name: [float(row["features"].get(name, 0.0)) for row in rows] for name in names}
    result: dict[str, float] = {}
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            correlation = _pearson(_ranks(values[left]), _ranks(values[right]))
            if correlation is not None:
                result[f"{left}::{right}"] = round(correlation, 8)
    return result


def _point_biserial(rows: list[dict[str, Any]], names: list[str], target: str) -> dict[str, float]:
    labels = [int(row[target]) for row in rows]
    result: dict[str, float] = {}
    for name in names:
        values = [float(row["features"].get(name, 0.0)) for row in rows]
        correlation = _pearson(values, [float(label) for label in labels])
        if correlation is not None:
            result[name] = round(correlation, 8)
    return result


def _redundancy_groups(
    pearson: dict[str, float], spearman: dict[str, float], *, threshold: float = 0.90
) -> list[list[str]]:
    edges: dict[str, set[str]] = {}
    for key in set(pearson) | set(spearman):
        if max(abs(pearson.get(key, 0.0)), abs(spearman.get(key, 0.0))) < threshold:
            continue
        left, right = key.split("::", 1)
        edges.setdefault(left, set()).add(right)
        edges.setdefault(right, set()).add(left)
    groups: list[list[str]] = []
    visited: set[str] = set()
    for root in sorted(edges):
        if root in visited:
            continue
        pending = [root]
        group: set[str] = set()
        while pending:
            current = pending.pop()
            if current in group:
                continue
            group.add(current)
            pending.extend(edges.get(current, set()) - group)
        visited.update(group)
        groups.append(sorted(group))
    return groups


def calibrate(
    path: Path, *, target: str, precision_floor: float, training_split: str | None = None
) -> dict[str, Any]:
    rows = _rows(path, target)
    if training_split is not None:
        rows = [row for row in rows if row.get("split") == training_split]
        if not rows:
            raise ValueError(f"no labelled {target} rows in split {training_split!r}")
    names = _feature_names(rows)
    intercept, weights = _fit(rows, names, target)
    threshold, metrics = _threshold(rows, names, target, intercept, weights, precision_floor)
    pearson = _correlations(rows, names)
    spearman = _spearman_correlations(rows, names)
    return {
        "version": "calibrated-policy/v1",
        "target": target,
        "training_rows": len(rows),
        "precision_floor": precision_floor,
        "threshold": threshold,
        "training_metrics": metrics,
        "intercept": round(intercept, 8),
        "coefficients": {
            name: round(weight, 8) for name, weight in zip(names, weights, strict=True)
        },
        "training_split": training_split,
        "pearson_correlations": pearson,
        "spearman_correlations": spearman,
        "point_biserial_target": _point_biserial(rows, names, target),
        "redundancy_groups": _redundancy_groups(pearson, spearman),
        "provenance": {
            "dataset_sha256": __import__("hashlib").sha256(path.read_bytes()).hexdigest()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--target", choices=("is_job", "relevant"), default="relevant")
    parser.add_argument("--precision-floor", type=float, default=0.90)
    parser.add_argument("--split", dest="training_split", default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 0 < args.precision_floor <= 1:
        raise ValueError("--precision-floor must be in (0, 1]")
    artifact = calibrate(
        args.dataset,
        target=args.target,
        precision_floor=args.precision_floor,
        training_split=args.training_split,
    )
    args.output.write_text(
        yaml.safe_dump(artifact, sort_keys=True, allow_unicode=True), encoding="utf-8"
    )
    print(json.dumps(artifact, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
