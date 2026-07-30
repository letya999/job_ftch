from __future__ import annotations

import pytest

from job_ftch.domain import ObservationLedgerEntry, RawItem, SourceKind, content_hash_for_raw_item


def _raw(text: str) -> RawItem:
    return RawItem(
        source_kind=SourceKind.DEBUG, source_name="ledger", external_id="same", text=text
    )


def test_content_identity_changes_when_the_locator_content_changes() -> None:
    first, changed = _raw("first"), _raw("changed")
    assert first.stable_id == changed.stable_id
    assert content_hash_for_raw_item(first) != content_hash_for_raw_item(changed)


def test_ledger_entry_validates_raw_content_provenance() -> None:
    raw = _raw("source text")
    entry = ObservationLedgerEntry(
        observation_id="observation-1",
        stable_id=raw.stable_id,
        content_hash=content_hash_for_raw_item(raw),
        decision_version="policy-v1",
        raw_item=raw,
    )
    assert entry.content_version == 1
    with pytest.raises(ValueError, match="content_hash"):
        entry.model_copy(update={"content_hash": "0" * 64}).__class__.model_validate(
            {**entry.model_dump(mode="json"), "content_hash": "0" * 64}
        )
