from __future__ import annotations

from paritylab.behavior_replay import BehaviorReplayIndex, behavior_signature
from paritylab.models import BehaviorEvent


def _events(session: str, *, offset: float = 0.0) -> list[BehaviorEvent]:
    return [
        BehaviorEvent(
            session_id=session,
            observed_at="2026-08-05T00:00:00Z",
            sequence=index,
            event_type="pointermove" if index < 8 else "click",
            client_ts_ms=index * 17.0 + offset,
            since_navigation_ms=index * 17.0 + offset,
            trusted=True,
            data={"x": index * 11, "y": index * 3},
        )
        for index in range(9)
    ]


def test_behavior_signature_is_start_time_invariant() -> None:
    first = behavior_signature(_events("one"))
    second = behavior_signature(_events("two", offset=500.0))
    assert first is not None and second is not None
    assert first.exact_sha256 == second.exact_sha256


async def test_replay_index_detects_distinct_session_exact_replay(tmp_path) -> None:
    index = BehaviorReplayIndex(tmp_path / "replay.jsonl")
    first = await index.assess_and_record("session-one", _events("session-one"))
    second = await index.assess_and_record("session-two", _events("session-two", offset=1000))
    assert first.exact_match is False
    assert second.exact_match is True
    assert second.nearest_similarity == 1.0
    content = (tmp_path / "replay.jsonl").read_text(encoding="utf-8")
    assert "session-one" not in content
    assert "session-two" not in content


async def test_replay_index_ignores_short_sequences(tmp_path) -> None:
    index = BehaviorReplayIndex(tmp_path / "replay.jsonl")
    assessment = await index.assess_and_record("short", _events("short")[:4])
    assert assessment.eligible is False
    assert not (tmp_path / "replay.jsonl").exists()
