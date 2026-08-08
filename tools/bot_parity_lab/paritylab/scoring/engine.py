from __future__ import annotations

from collections import Counter

from paritylab.models import (
    GateDisposition,
    Finding,
    ScoreSummary,
    SessionState,
    SignalClass,
)
from paritylab.reputation import OfflineIPReputation
from paritylab.scoring.common import HARD_WEIGHT, LOW_WEIGHT, MEDIUM_WEIGHT

from paritylab.scoring.behavior import _behavior_findings
from paritylab.scoring.catalog import _catalog_findings
from paritylab.scoring.capabilities import _capability_findings
from paritylab.scoring.integrity import _session_integrity_findings
from paritylab.scoring.network import _network_findings, _tls_findings
from paritylab.scoring.playground import _playground_findings
from paritylab.scoring.protocol import _protocol_and_reputation_findings
from paritylab.scoring.realm import _cross_realm_findings
from paritylab.scoring.rendering import _rendering_findings
from paritylab.scoring.runtime import _runtime_findings
from paritylab.scoring.timing import _timing_findings
from paritylab.scoring.webrtc import _webrtc_findings


def score_session(
    session: SessionState,
    *,
    reputation: OfflineIPReputation,
) -> tuple[list[Finding], ScoreSummary]:
    findings = [
        *_network_findings(session),
        *_tls_findings(session),
        *_runtime_findings(session),
        *_capability_findings(session),
        *_rendering_findings(session),
        *_timing_findings(session),
        *_webrtc_findings(session),
        *_session_integrity_findings(session),
        *_cross_realm_findings(session),
        *_behavior_findings(session),
        *_catalog_findings(session),
        *_protocol_and_reputation_findings(session, reputation),
        *_playground_findings(session),
    ]
    findings.sort(
        key=lambda item: (
            {
                SignalClass.HARD_BOT: 0,
                SignalClass.MEDIUM: 1,
                SignalClass.LOW: 2,
                SignalClass.INFO: 3,
            }[item.signal_class],
            item.code,
        )
    )
    counts = Counter(item.signal_class for item in findings)
    score = min(
        100,
        counts[SignalClass.HARD_BOT] * HARD_WEIGHT
        + counts[SignalClass.MEDIUM] * MEDIUM_WEIGHT
        + counts[SignalClass.LOW] * LOW_WEIGHT,
    )
    has_gate_failure = counts[SignalClass.HARD_BOT] > 0 or counts[SignalClass.MEDIUM] > 0
    if session.expected_failure and has_gate_failure:
        disposition = GateDisposition.EXPECTED_FAIL
        gate_reason = "Negative control produced medium/high findings as intended."
    elif session.gate_enabled and has_gate_failure:
        disposition = GateDisposition.FAIL
        gate_reason = "Gate enabled and at least one medium/high finding was produced."
    else:
        disposition = GateDisposition.PASS
        gate_reason = (
            "No medium/high findings were produced."
            if not has_gate_failure
            else "Findings were recorded, but this comparison client is not configured as a blocking gate."
        )
    summary = ScoreSummary(
        score=score,
        hard_count=counts[SignalClass.HARD_BOT],
        medium_count=counts[SignalClass.MEDIUM],
        low_count=counts[SignalClass.LOW],
        info_count=counts[SignalClass.INFO],
        disposition=disposition,
        gate_reason=gate_reason,
    )
    return findings, summary
