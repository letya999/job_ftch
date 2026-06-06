from __future__ import annotations

import pytest

from application import ProcessingContext
from application.outcomes import OutcomeKind, RejectReason
from domain import RawItem, SourceKind
from nodes import ValidateRawNode


@pytest.mark.asyncio
async def test_validate_raw_node_passes_useful_raw_item() -> None:
    item = RawItem(
        source_kind=SourceKind.DEBUG,
        source_name="fixture",
        external_id="item-1",
        text="Hiring AI engineer",
    )

    outcome = await ValidateRawNode().process(item, ProcessingContext(max_text_length=100))

    assert outcome.kind is OutcomeKind.PASS
    assert outcome.item == item


@pytest.mark.asyncio
async def test_validate_raw_node_rejects_text_over_context_limit() -> None:
    item = RawItem(
        source_kind=SourceKind.DEBUG,
        source_name="fixture",
        external_id="item-1",
        text="x" * 11,
    )

    outcome = await ValidateRawNode().process(item, ProcessingContext(max_text_length=10))

    assert outcome.kind is OutcomeKind.QUARANTINE
    assert outcome.reason is RejectReason.TEXT_TOO_LONG
    assert outcome.metadata["text_length"] == 11
    assert outcome.metadata["max_text_length"] == 10


@pytest.mark.asyncio
async def test_validate_raw_node_rejects_missing_locator_from_constructed_item() -> None:
    item = RawItem.model_construct(
        stable_id="",
        source_kind=SourceKind.DEBUG,
        source_name="fixture",
        external_id=None,
        url=None,
        text="Hiring AI engineer",
        metadata={},
    )

    outcome = await ValidateRawNode().process(item, ProcessingContext())

    assert outcome.kind is OutcomeKind.QUARANTINE
    assert outcome.reason is RejectReason.MISSING_LOCATOR
