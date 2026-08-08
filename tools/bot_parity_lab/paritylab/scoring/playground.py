from __future__ import annotations

import hashlib
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from itertools import pairwise
from typing import Any

from paritylab.legacy_snapshot import score_snapshot as score_catalog_snapshot

from paritylab.models import (
    Finding,
    GateDisposition,
    JsonValue,
    ScoreSummary,
    SessionState,
    SignalClass,
)
from paritylab.reputation import OfflineIPReputation
from paritylab.scoring.common import (
    CATALOG_SEVERITY_CLASS,
    HARD_WEIGHT,
    LOW_WEIGHT,
    MEDIUM_WEIGHT,
    _catalog_snapshot,
    _deep_get,
    _finding,
    _header_map,
    _light_interaction,
    _light_request,
    _light_window,
    _realm_map,
)

def _playground_findings(session: SessionState) -> list[Finding]:
    findings: list[Finding] = []
    hard_overrides = [
        item for item in session.gate_decisions if item.reason_code == "LIVE_HARD_RISK"
    ]
    if hard_overrides:
        findings.append(
            _finding(
                SignalClass.HARD_BOT,
                "PLAYGROUND_CLEARANCE_RISK_OVERRIDE",
                "Live risk invalidated protected access",
                "A valid or attempted clearance could not override positive hard automation evidence.",
                evidence={
                    "count": len(hard_overrides),
                    "risk_codes": sorted(
                        {
                            code
                            for item in hard_overrides
                            for code in item.detail.split(",")
                            if code
                        }
                    ),
                },
            )
        )
    medium_escalations = [
        item for item in session.gate_decisions if item.reason_code == "LIVE_MEDIUM_RISK"
    ]
    if medium_escalations:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "PLAYGROUND_LIVE_RISK_ESCALATION",
                "Correlated live risk escalated the challenge",
                "Multiple observed suspicious signals caused challenge escalation independently of clearance state.",
                evidence={
                    "count": len(medium_escalations),
                    "risk_codes": sorted(
                        {
                            code
                            for item in medium_escalations
                            for code in item.detail.split(",")
                            if code
                        }
                    ),
                },
            )
        )
    if session.trap_hits:
        findings.append(
            _finding(
                SignalClass.HARD_BOT,
                "PLAYGROUND_TRAP_HIT",
                "Hidden honeypot path was requested",
                "The session requested a path that is hidden from visible navigation and "
                "disallowed in robots.txt. Only non-visual clients reach it.",
                evidence={"paths": [str(item) for item in session.trap_hits[:24]]},
            )
        )
    rejected_clearance = [
        record
        for record in session.challenges
        if record.kind == "clearance" and record.outcome.value == "rejected"
    ]
    if rejected_clearance:
        findings.append(
            _finding(
                SignalClass.HARD_BOT,
                "PLAYGROUND_CLEARANCE_REJECTED",
                "Clearance token failed verification",
                "A presented clearance token had a bad signature, wrong session binding, or was "
                "revoked. Token values are not retained; only hash prefixes are recorded.",
                evidence={
                    "count": len(rejected_clearance),
                    "hash_prefixes": [record.challenge_id_hash for record in rejected_clearance[:8]],
                },
            )
        )
    pow_attempts = [
        record.attempts
        for record in session.challenges
        if record.kind == "pow" and record.attempts > 10
    ]
    if pow_attempts:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "PLAYGROUND_POW_BURST",
                "Repeated proof-of-work attempts",
                "A local PoW challenge needed an unusually high attempt count, which can indicate "
                "a brute-force or replay-style solver rather than a normal browser loop.",
                evidence={"max_attempts": max(pow_attempts)},
            )
        )
    if session.intent is not None:
        intent = session.intent
        findings.append(
            _finding(
                SignalClass.INFO,
                "PLAYGROUND_INTENT",
                "Scrape intent classified",
                "The owned protected-site model classified what this session tried to parse.",
                evidence={
                    "intent": intent.intent,
                    "confidence": intent.confidence,
                    "distinct_jobs": intent.distinct_jobs,
                    "listing_pages": intent.listing_pages,
                    "api_requests": intent.api_requests,
                    "coverage_ratio": intent.coverage_ratio,
                    "velocity_rps": intent.velocity_rps,
                    "median_gap_ms": intent.median_gap_ms,
                    "surfaces": dict(intent.surfaces),
                },
            )
        )
    if session.gate_decisions:
        counts = Counter(item.decision.value for item in session.gate_decisions)
        findings.append(
            _finding(
                SignalClass.INFO,
                "PLAYGROUND_GATE_DECISIONS",
                "Edge gate decision distribution",
                "The local Akamai/Cloudflare-style decision layer recorded every allow/challenge/"
                "deny/tarpit verdict for this session.",
                evidence={"counts": dict(counts), "total": len(session.gate_decisions)},
            )
        )
    probe_realms = {probe.realm for probe in session.probes}
    if probe_realms:
        findings.append(
            _finding(
                SignalClass.INFO,
                "PLAYGROUND_FINGERPRINT_COVERAGE",
                "Fingerprint collection coverage",
                "Realms that submitted fingerprint probes for this session.",
                evidence={"realms": sorted(probe_realms), "count": len(probe_realms)},
            )
        )
    return findings
