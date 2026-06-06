from __future__ import annotations

from application import NodeOutcome, OutcomeKind, RejectReason


def test_node_outcome_factories_create_stable_shapes() -> None:
    item = {"id": "1"}

    passed = NodeOutcome.pass_(item)
    dropped = NodeOutcome.drop(reason=RejectReason.NON_JOB, message="noise")
    quarantined = NodeOutcome.quarantine(
        item=item,
        reason=RejectReason.INVALID_RAW_ITEM,
        message="bad payload",
        metadata={"line": 3},
    )
    failed = NodeOutcome.fail(reason=RejectReason.NODE_FAILED, message="boom")

    assert passed.kind is OutcomeKind.PASS
    assert passed.item == item
    assert dropped.kind is OutcomeKind.DROP
    assert dropped.reason is RejectReason.NON_JOB
    assert quarantined.kind is OutcomeKind.QUARANTINE
    assert quarantined.metadata == {"line": 3}
    assert failed.kind is OutcomeKind.FAIL
