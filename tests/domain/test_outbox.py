from job_ftch.domain import OutboxRecord, OutboxState, delivery_idempotency_key


def test_delivery_idempotency_key_is_deterministic_and_sink_scoped() -> None:
    first = delivery_idempotency_key(
        content_hash="a" * 64, decision_version="v1", sink_name="telegram"
    )
    assert first == delivery_idempotency_key(
        content_hash="a" * 64, decision_version="v1", sink_name="telegram"
    )
    assert first != delivery_idempotency_key(
        content_hash="a" * 64, decision_version="v1", sink_name="json"
    )


def test_outbox_starts_at_decided() -> None:
    record = OutboxRecord(
        outbox_id="o1",
        observation_id="obs1",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram",
        idempotency_key="b" * 64,
    )
    assert record.state is OutboxState.DECIDED
