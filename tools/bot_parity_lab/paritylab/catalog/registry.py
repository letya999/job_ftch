from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any, TypeVar

from paritylab.catalog.schema import (
    CountermeasureSpec,
    CoverageStatus,
    FindingSpec,
    MechanicSpec,
    SurfaceSpec,
)

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Catalog:
    version: str
    surfaces: tuple[SurfaceSpec, ...]
    findings: tuple[FindingSpec, ...]
    mechanics: tuple[MechanicSpec, ...]
    countermeasures: tuple[CountermeasureSpec, ...]

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        surface_ids = _unique_ids(self.surfaces, "id", "surface", errors)
        finding_codes = _unique_ids(self.findings, "code", "finding", errors)
        mechanic_ids = _unique_ids(self.mechanics, "id", "mechanic", errors)
        countermeasure_ids = _unique_ids(
            self.countermeasures, "id", "countermeasure", errors
        )
        for finding in self.findings:
            if finding.surface not in surface_ids:
                errors.append(f"finding {finding.code} references unknown surface {finding.surface}")
            errors.extend(
                f"finding {finding.code} references unknown mechanic {item}"
                for item in finding.detects
                if item not in mechanic_ids
            )
            errors.extend(
                f"finding {finding.code} references unknown countermeasure {item}"
                for item in finding.countermeasures
                if item not in countermeasure_ids
            )
        for mechanic in self.mechanics:
            errors.extend(
                f"mechanic {mechanic.id} references unknown surface {item}"
                for item in mechanic.surfaces
                if item not in surface_ids
            )
            errors.extend(
                f"mechanic {mechanic.id} references unknown countermeasure {item}"
                for item in mechanic.countermeasures
                if item not in countermeasure_ids
            )
        if not finding_codes:
            errors.append("catalog contains no findings")
        return tuple(errors)

    def coverage(self) -> dict[str, Any]:
        status_counts = {
            status.value: sum(item.status is status for item in self.surfaces)
            for status in CoverageStatus
        }
        implemented = status_counts[CoverageStatus.IMPLEMENTED.value]
        partial = status_counts[CoverageStatus.PARTIAL.value]
        total = len(self.surfaces)
        return {
            "version": self.version,
            "surface_count": total,
            "finding_count": len(self.findings),
            "mechanic_count": len(self.mechanics),
            "countermeasure_count": len(self.countermeasures),
            "surface_status": status_counts,
            "weighted_surface_coverage": round((implemented + partial * 0.5) / total, 4)
            if total
            else 0.0,
        }


def _unique_ids(items: tuple[T, ...], field: str, kind: str, errors: list[str]) -> set[str]:
    values: set[str] = set()
    for item in items:
        value = str(getattr(item, field))
        if value in values:
            errors.append(f"duplicate {kind} id: {value}")
        values.add(value)
    return values


@lru_cache(maxsize=1)
def load_catalog() -> Catalog:
    resource = files("paritylab.catalog").joinpath("catalog.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    catalog = Catalog(
        version=str(payload["version"]),
        surfaces=tuple(SurfaceSpec.from_dict(item) for item in payload["surfaces"]),
        findings=tuple(FindingSpec.from_dict(item) for item in payload["findings"]),
        mechanics=tuple(MechanicSpec.from_dict(item) for item in payload["mechanics"]),
        countermeasures=tuple(
            CountermeasureSpec.from_dict(item) for item in payload["countermeasures"]
        ),
    )
    errors = catalog.validate()
    if errors:
        raise ValueError("invalid parity-lab catalog:\n" + "\n".join(errors))
    return catalog
