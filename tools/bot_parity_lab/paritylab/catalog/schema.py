from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class CoverageStatus(StrEnum):
    IMPLEMENTED = "implemented"
    PARTIAL = "partial"
    PLANNED = "planned"
    KNOWLEDGE_ONLY = "knowledge-only"
    UNAVAILABLE = "unavailable"
    OBSOLETE = "obsolete"


class Stability(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    EXPERIMENTAL = "experimental"


@dataclass(frozen=True, slots=True)
class Reference:
    title: str
    url: str
    kind: str
    checked_at: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Reference:
        return cls(**value)


@dataclass(frozen=True, slots=True)
class SurfaceSpec:
    id: str
    title: str
    family: str
    status: CoverageStatus
    stability: Stability
    description: str
    collectors: tuple[str, ...]
    realms: tuple[str, ...]
    browsers: tuple[str, ...]
    privacy: str
    references: tuple[Reference, ...] = ()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SurfaceSpec:
        data = dict(value)
        data["status"] = CoverageStatus(data["status"])
        data["stability"] = Stability(data["stability"])
        data["collectors"] = tuple(data.get("collectors", ()))
        data["realms"] = tuple(data.get("realms", ()))
        data["browsers"] = tuple(data.get("browsers", ()))
        data["references"] = tuple(Reference.from_dict(item) for item in data.get("references", ()))
        return cls(**data)


@dataclass(frozen=True, slots=True)
class FindingSpec:
    code: str
    title: str
    surface: str
    status: CoverageStatus
    default_class: str
    detects: tuple[str, ...]
    benign_causes: tuple[str, ...]
    countermeasures: tuple[str, ...]
    fixtures: tuple[str, ...]
    tests: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> FindingSpec:
        data = dict(value)
        data["status"] = CoverageStatus(data["status"])
        for field in ("detects", "benign_causes", "countermeasures", "fixtures", "tests"):
            data[field] = tuple(data.get(field, ()))
        return cls(**data)


@dataclass(frozen=True, slots=True)
class MechanicSpec:
    id: str
    title: str
    category: str
    status: CoverageStatus
    description: str
    observable_consequences: tuple[str, ...]
    surfaces: tuple[str, ...]
    countermeasures: tuple[str, ...]
    residual_risk: str
    references: tuple[Reference, ...] = ()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MechanicSpec:
        data = dict(value)
        data["status"] = CoverageStatus(data["status"])
        for field in ("observable_consequences", "surfaces", "countermeasures"):
            data[field] = tuple(data.get(field, ()))
        data["references"] = tuple(Reference.from_dict(item) for item in data.get("references", ()))
        return cls(**data)


@dataclass(frozen=True, slots=True)
class CountermeasureSpec:
    id: str
    title: str
    description: str
    validates_with: tuple[str, ...]
    limitations: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CountermeasureSpec:
        data = dict(value)
        data["validates_with"] = tuple(data.get("validates_with", ()))
        data["limitations"] = tuple(data.get("limitations", ()))
        return cls(**data)
