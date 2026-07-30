"""Export a calibration feature matrix from typed-evidence pipeline replay traces.

The exporter is deliberately replay-only: it never creates evidence, calls an
LLM, or uses a model's final decision as a feature.  Parent IDs are retained as
groups so a later calibration split cannot leak candidate spans from one post.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

_ATOM_NUMERIC = ("strength", "reliability")
_ASSESSMENT_NUMERIC = (
    "belief_true",
    "certainty",
    "coverage",
    "conflict",
    "support_mass",
    "contradiction_mass",
)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _typed_trace(result: dict[str, Any]) -> dict[str, Any] | None:
    trace = result.get("decision_trace")
    if not isinstance(trace, dict):
        return None
    typed = trace.get("typed_evidence")
    return typed if isinstance(typed, dict) else None


def _features(typed: dict[str, Any]) -> dict[str, float]:
    """Aggregate only raw typed-evidence fields, respecting independence keys."""
    features: dict[str, float] = {}
    atoms = typed.get("atoms")
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    if isinstance(atoms, list):
        for atom in atoms:
            if not isinstance(atom, dict):
                continue
            claim = atom.get("claim")
            polarity = atom.get("polarity")
            key = atom.get("independence_key")
            if (
                not isinstance(claim, str)
                or not claim
                or not isinstance(polarity, str)
                or not polarity
                or not isinstance(key, str)
                or not key
            ):
                continue
            group_key = (claim, polarity, key)
            previous = grouped.get(group_key)
            strength = _number(atom.get("strength")) or 0.0
            reliability = _number(atom.get("reliability")) or 0.0
            if previous is None or strength * reliability > (
                (_number(previous.get("strength")) or 0.0)
                * (_number(previous.get("reliability")) or 0.0)
            ):
                grouped[group_key] = atom
    atom_values: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for (claim, polarity, _), atom in grouped.items():
        atom_values[(claim, polarity)].append(atom)
    for (claim, polarity), values in atom_values.items():
        prefix = f"atom.{claim}.{polarity}"
        features[f"{prefix}.count"] = float(len(values))
        for field in _ATOM_NUMERIC:
            numbers = [_number(value.get(field)) for value in values]
            numeric = [value for value in numbers if value is not None]
            if numeric:
                features[f"{prefix}.{field}_max"] = max(numeric)
                features[f"{prefix}.{field}_sum"] = sum(numeric)

    assessments = typed.get("assessments")
    assessment_values: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if isinstance(assessments, list):
        for assessment in assessments:
            claim = assessment.get("claim") if isinstance(assessment, dict) else None
            if isinstance(claim, str) and claim:
                assessment_values[claim].append(assessment)
    for claim, values in assessment_values.items():
        prefix = f"assessment.{claim}"
        for field in _ASSESSMENT_NUMERIC:
            numeric = [
                number for value in values if (number := _number(value.get(field))) is not None
            ]
            if numeric:
                features[f"{prefix}.{field}_max"] = max(numeric)
                features[f"{prefix}.{field}_min"] = min(numeric)
    features["evidence.degradation_count"] = float(
        len(typed.get("degradation_reasons", ()))
        if isinstance(typed.get("degradation_reasons"), list)
        else 0
    )
    return dict(sorted(features.items()))


def export(payload: dict[str, Any], *, target: str) -> tuple[list[dict[str, Any]], int]:
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("replay payload must contain a results list")
    output: list[dict[str, Any]] = []
    skipped = 0
    for result in results:
        if not isinstance(result, dict):
            continue
        label = result.get(target)
        if type(label) is not int or label not in (0, 1):
            skipped += 1
            continue
        typed = _typed_trace(result)
        if typed is None:
            skipped += 1
            continue
        stable_id = result.get("stable_id")
        parent_id = result.get("parent_stable_id", stable_id)
        if (
            not isinstance(stable_id, str)
            or not stable_id
            or not isinstance(parent_id, str)
            or not parent_id
        ):
            skipped += 1
            continue
        output.append(
            {
                "stable_id": stable_id,
                "parent_stable_id": parent_id,
                "group": parent_id,
                "source_kind": result.get("source_kind"),
                "source_name": result.get("source_name"),
                target: label,
                "policy_version": typed.get("policy_version"),
                "features": _features(typed),
            }
        )
    if not output:
        raise ValueError("replay has no binary-labelled results with typed_evidence")
    return output, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("replay", type=Path)
    parser.add_argument("--target", default="gold_relevant")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.replay.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("replay must be a JSON object")
    rows, skipped = export(payload, target=args.target)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(f"wrote {len(rows)} rows to {args.output} (skipped={skipped})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
