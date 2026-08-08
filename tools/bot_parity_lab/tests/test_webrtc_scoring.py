from __future__ import annotations

from paritylab.models import ProbeRecord, SessionState
from paritylab.reputation import OfflineIPReputation
from paritylab.scoring import score_session


def test_webrtc_coherence_detects_structural_conflicts(tmp_path) -> None:
    session = SessionState("webrtc", "test", "test", False, False)
    session.probes.append(
        ProbeRecord(
            session.session_id,
            "2026-08-05T00:00:00Z",
            "deep",
            1,
            {
                "webrtc": {
                    "supported": True,
                    "candidateCount": 2,
                    "types": {"host": 1},
                    "addressClasses": {},
                    "candidateIntervals": [-1.0],
                    "gatheringDurationMs": 1500.0,
                    "finalGatheringState": "gathering",
                    "sdpLineCount": 0,
                    "sdpShapeHash": "",
                }
            },
        )
    )
    findings, _ = score_session(
        session, reputation=OfflineIPReputation(tmp_path / "reputation.json")
    )
    codes = {item.code for item in findings}
    assert {
        "WEBRTC_CANDIDATE_COUNT_CONFLICT",
        "WEBRTC_GATHERING_TIMEOUT",
        "WEBRTC_SDP_SHAPE_MISSING",
        "WEBRTC_CANDIDATE_TIME_REGRESSION",
        "WEBRTC_HOST_ADDRESS_CLASS_MISSING",
    } <= codes


def test_webrtc_no_candidates_is_informational(tmp_path) -> None:
    session = SessionState("webrtc-private", "test", "test", False, False)
    session.probes.append(
        ProbeRecord(
            session.session_id,
            "2026-08-05T00:00:00Z",
            "deep",
            1,
            {
                "webrtc": {
                    "supported": True,
                    "candidateCount": 0,
                    "types": {},
                    "candidateIntervals": [],
                    "gatheringDurationMs": 20.0,
                    "finalGatheringState": "complete",
                    "sdpLineCount": 10,
                    "sdpShapeHash": "abc",
                }
            },
        )
    )
    findings, _ = score_session(
        session, reputation=OfflineIPReputation(tmp_path / "reputation.json")
    )
    finding = next(item for item in findings if item.code == "WEBRTC_NO_CANDIDATES")
    assert finding.signal_class.value == "informational"
