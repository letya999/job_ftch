from __future__ import annotations

from pathlib import Path

from paritylab.models import ProbeRecord, SessionState
from paritylab.reputation import OfflineIPReputation
from paritylab.scoring import score_session


def _score(tmp_path: Path, timing: dict[str, object]) -> set[str]:
    session = SessionState("timing", "test", "test", False, False)
    session.probes.extend(
        [
            ProbeRecord(
                session.session_id,
                "2026-08-05T00:00:00Z",
                "window",
                1,
                {"performance": {"timeOrigin": 1000.0}},
            ),
            ProbeRecord(
                session.session_id,
                "2026-08-05T00:00:01Z",
                "deep",
                2,
                {"timing": timing},
            ),
        ]
    )
    findings, _ = score_session(
        session, reputation=OfflineIPReputation(tmp_path / "reputation.json")
    )
    return {item.code for item in findings}


def test_timing_integrity_accepts_coherent_event_loop(tmp_path) -> None:
    codes = _score(
        tmp_path,
        {
            "nowResolutionMs": 0.1,
            "dateDriftMs": 0.2,
            "dateDriftRangeMs": 0.5,
            "monotonicViolations": 0,
            "taskOrder": ["promise", "microtask", "message", "timeout"],
            "rafIntervals": [16.6, 16.7, 16.6],
            "timeOrigin": 1000.0,
        },
    )
    assert not any(code.startswith("TIMING_") for code in codes)


def test_timing_integrity_detects_clock_and_task_conflicts(tmp_path) -> None:
    codes = _score(
        tmp_path,
        {
            "nowResolutionMs": 2.0,
            "dateDriftMs": 250.0,
            "dateDriftRangeMs": 30.0,
            "monotonicViolations": 2,
            "taskOrder": ["message", "promise", "microtask", "timeout"],
            "rafIntervals": [16.6, 0.0],
            "timeOrigin": 1200.0,
        },
    )
    assert {
        "TIMING_MONOTONIC_REGRESSION",
        "TIMING_CLOCK_ORIGIN_CONFLICT",
        "TIMING_TASK_ORDER_CONFLICT",
        "TIMING_RAF_NON_MONOTONIC",
        "TIMING_REALM_ORIGIN_DRIFT",
        "TIMING_COARSE_RESOLUTION",
    } <= codes
