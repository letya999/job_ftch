"""Load the small, typed evidence calibration table."""

from __future__ import annotations

from pathlib import Path

from job_ftch.domain import ClaimKind, ClaimParameters, SourceFamily


def load_evidence_parameters(
    path: Path | None = None,
) -> dict[tuple[ClaimKind, SourceFamily], ClaimParameters]:
    resolved = path or Path("config/evidence_policy.yaml")
    if not resolved.is_file():
        return {}
    import yaml

    raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    result: dict[tuple[ClaimKind, SourceFamily], ClaimParameters] = {}
    for claim_name, families in (raw.get("claims") or {}).items():
        try:
            claim = ClaimKind(claim_name)
        except ValueError:
            continue
        for family_name, values in (families or {}).items():
            try:
                family = SourceFamily(family_name)
                result[(claim, family)] = ClaimParameters.model_validate(values or {})
            except (TypeError, ValueError):
                continue
    return result
