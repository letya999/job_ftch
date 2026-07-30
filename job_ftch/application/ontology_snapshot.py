"""Pure, replay-safe comparison helpers for immutable ontology snapshots."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from job_ftch.domain import OntologySnapshot


@dataclass(frozen=True)
class OntologyChange:
    """One deterministic JSON-pointer change between two ontology views."""

    path: str
    before: str | int | float | bool | None
    after: str | int | float | bool | None


@dataclass(frozen=True)
class OntologySnapshotDiff:
    """Versioned, deterministic description of a tenant/profile ontology update."""

    tenant_id: str
    profile_id: str
    previous_version: str
    current_version: str
    changes: tuple[OntologyChange, ...]

    @property
    def changed(self) -> bool:
        return bool(self.changes)


@dataclass(frozen=True)
class OntologyItemReference:
    """Minimal replay artifact used to determine whether a record needs review.

    ``terms`` must contain only terms/evidence actually used by a previous run;
    callers must not insert inferred aliases here.
    """

    item_id: str
    ontology_version: str
    terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class AffectedOntologyItem:
    """A prior-run item potentially affected by an ontology update."""

    item_id: str
    matched_terms: tuple[str, ...]
    requires_replay: bool = True


@dataclass(frozen=True)
class OntologyAffectedItemReport:
    """Stable report selecting new-version records for replay or human audit."""

    previous_version: str
    current_version: str
    items: tuple[AffectedOntologyItem, ...]


def diff_ontology_snapshots(
    previous: OntologySnapshot, current: OntologySnapshot
) -> OntologySnapshotDiff:
    """Compare snapshots recursively without consulting mutable ontology state."""
    if (previous.tenant_id, previous.profile_id) != (current.tenant_id, current.profile_id):
        raise ValueError("ontology snapshots must belong to the same tenant and profile")
    before = json.loads(previous.payload_json)
    after = json.loads(current.payload_json)
    changes = tuple(_diff_values(before, after))
    return OntologySnapshotDiff(
        tenant_id=previous.tenant_id,
        profile_id=previous.profile_id,
        previous_version=previous.version,
        current_version=current.version,
        changes=changes,
    )


def build_affected_item_report(
    diff: OntologySnapshotDiff, items: Iterable[OntologyItemReference]
) -> OntologyAffectedItemReport:
    """Select new-version records and identify changed terms present in their evidence.

    Every record processed under the preceding version remains replayable even
    when no direct lexical overlap is available.  This deliberately avoids a
    false claim that an alias change cannot affect an item merely because the
    new extraction did not preserve a matching term.
    """
    changed_terms = _changed_terms(diff.changes)
    affected = [
        AffectedOntologyItem(
            item_id=item.item_id,
            matched_terms=tuple(
                term
                for term in sorted({term.casefold() for term in item.terms})
                if term in changed_terms
            ),
        )
        for item in items
        if item.ontology_version == diff.previous_version
    ]
    return OntologyAffectedItemReport(
        previous_version=diff.previous_version,
        current_version=diff.current_version,
        items=tuple(sorted(affected, key=lambda item: item.item_id)),
    )


def _diff_values(before: Any, after: Any, path: str = "") -> list[OntologyChange]:
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[OntologyChange] = []
        for key in sorted(set(before) | set(after)):
            changes.extend(_diff_values(before.get(key), after.get(key), f"{path}/{key}"))
        return changes
    if isinstance(before, list) and isinstance(after, list):
        if before == after:
            return []
        return [OntologyChange(path or "/", _canonical_value(before), _canonical_value(after))]
    if before == after:
        return []
    return [OntologyChange(path or "/", _scalar_value(before), _scalar_value(after))]


def _scalar_value(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return _canonical_value(value)


def _canonical_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _changed_terms(changes: Iterable[OntologyChange]) -> frozenset[str]:
    values = (change.before for change in changes), (change.after for change in changes)
    return frozenset(
        value.casefold()
        for side in values
        for value in side
        if isinstance(value, str) and value and not value.startswith(("{", "["))
    )
