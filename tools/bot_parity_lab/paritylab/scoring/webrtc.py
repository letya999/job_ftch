from __future__ import annotations

from collections.abc import Mapping

from paritylab.models import Finding, SessionState, SignalClass
from paritylab.scoring.common import _deep_get, _finding, _realm_map


def _webrtc_findings(session: SessionState) -> list[Finding]:
    evidence = _deep_get(_realm_map(session).get("deep", {}), "webrtc", {})
    if not isinstance(evidence, Mapping) or evidence.get("supported") is not True:
        return [
            _finding(
                SignalClass.LOW,
                "WEBRTC_PROBE_UNAVAILABLE",
                "WebRTC network probe unavailable",
                "ICE gathering and SDP shape could not be measured; browser policy may disable WebRTC.",
                realms=["deep"],
            )
        ]
    findings: list[Finding] = []
    count = evidence.get("candidateCount")
    types = evidence.get("types")
    if isinstance(count, int) and isinstance(types, Mapping):
        typed = sum(value for value in types.values() if isinstance(value, int))
        if typed != count:
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "WEBRTC_CANDIDATE_COUNT_CONFLICT",
                    "ICE candidate counts are inconsistent",
                    "The candidate total differs from the sum of normalized candidate types.",
                    evidence={"candidate_count": count, "typed_count": typed},
                    realms=["deep"],
                )
            )
    if count == 0:
        findings.append(
            _finding(
                SignalClass.INFO,
                "WEBRTC_NO_CANDIDATES",
                "ICE gathering exposed no candidates",
                "No ICE candidates were exposed; mDNS, enterprise policy or privacy settings may cause this.",
                realms=["deep"],
            )
        )
    final_state = evidence.get("finalGatheringState")
    duration = evidence.get("gatheringDurationMs")
    if final_state != "complete" and isinstance(duration, (int, float)) and duration >= 1400:
        findings.append(
            _finding(
                SignalClass.LOW,
                "WEBRTC_GATHERING_TIMEOUT",
                "ICE gathering did not complete",
                "The bounded gathering window elapsed before the peer connection reached complete.",
                evidence={"state": str(final_state), "duration_ms": round(float(duration), 3)},
                realms=["deep"],
            )
        )
    if not evidence.get("sdpShapeHash") or not isinstance(evidence.get("sdpLineCount"), int):
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "WEBRTC_SDP_SHAPE_MISSING",
                "SDP capability shape missing",
                "The local offer did not yield a bounded SDP structure hash and line count.",
                realms=["deep"],
            )
        )
    intervals = evidence.get("candidateIntervals")
    if isinstance(intervals, list) and any(
        isinstance(value, (int, float)) and value < 0 for value in intervals
    ):
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "WEBRTC_CANDIDATE_TIME_REGRESSION",
                "ICE candidate timestamps regress",
                "Candidate events are not ordered by the page monotonic clock.",
                realms=["deep"],
            )
        )
    if isinstance(types, Mapping) and types.get("host", 0):
        classes = evidence.get("addressClasses")
        if not isinstance(classes, Mapping) or not classes:
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "WEBRTC_HOST_ADDRESS_CLASS_MISSING",
                    "Host candidate lacks privacy-safe address class",
                    "Host ICE candidates were counted without any mDNS/IP-family classification.",
                    realms=["deep"],
                )
            )
    return findings
