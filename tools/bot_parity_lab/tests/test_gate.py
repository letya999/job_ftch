from __future__ import annotations

from paritylab.gate import GateEngine, GatePolicy
from paritylab.models import GateDecision, RequestRecord


def _request(seq: int) -> RequestRecord:
    return RequestRecord(
        request_id=f"req-{seq}",
        session_id="session-a",
        observed_at="2026-08-05T00:00:00.000+00:00",
        monotonic_ns=1_000_000_000 + seq * 10_000_000,
        method="GET",
        path="/jobs",
        query="",
        scheme="https",
        http_version="2",
        client_host="127.0.0.1",
        client_port=40000 + seq,
        connection_id=None,
        tls_ja3=None,
        tls_ja4=None,
        headers=(),
        response_status=200,
        response_headers=(),
        duration_ms=1.0,
    )


def test_exempt_and_unprotected_paths_are_allowed() -> None:
    gate = GateEngine()

    assert gate.decide(
        path="/robots.txt",
        requests=[],
        ja3=None,
        ja4=None,
        clearance_valid=False,
        pow_failures=0,
    ).decision is GateDecision.ALLOW
    assert gate.decide(
        path="/public",
        requests=[],
        ja3=None,
        ja4=None,
        clearance_valid=False,
        pow_failures=0,
    ).reason_code == "NO_POLICY_MATCH"


def test_clearance_and_fingerprint_policy_decisions() -> None:
    gate = GateEngine(
        GatePolicy(
            ja3_deny=frozenset({"deny-ja3"}),
            ja4_deny=frozenset({"deny-ja4"}),
            ja3_challenge=frozenset({"challenge-ja3"}),
        )
    )

    assert gate.decide(
        path="/jobs",
        requests=[],
        ja3="deny-ja3",
        ja4=None,
        clearance_valid=False,
        pow_failures=0,
    ).decision is GateDecision.DENY
    assert gate.decide(
        path="/jobs",
        requests=[],
        ja3=None,
        ja4="deny-ja4",
        clearance_valid=False,
        pow_failures=0,
    ).decision is GateDecision.DENY
    assert gate.decide(
        path="/jobs",
        requests=[],
        ja3=None,
        ja4=None,
        clearance_valid=True,
        pow_failures=0,
    ).reason_code == "CLEARANCE_VALID"
    assert gate.decide(
        path="/jobs",
        requests=[],
        ja3="challenge-ja3",
        ja4=None,
        clearance_valid=False,
        pow_failures=0,
    ).decision is GateDecision.INTERACTIVE_CHALLENGE


def test_rate_limit_and_tarpit_precede_clearance_challenge() -> None:
    gate = GateEngine(GatePolicy(max_requests_per_window=2, tarpit_multiplier=2.0))

    limited = gate.decide(
        path="/jobs",
        requests=[_request(1), _request(2), _request(3)],
        ja3=None,
        ja4=None,
        clearance_valid=False,
        pow_failures=0,
    )
    tarpit = gate.decide(
        path="/jobs",
        requests=[_request(index) for index in range(1, 7)],
        ja3=None,
        ja4=None,
        clearance_valid=False,
        pow_failures=0,
    )

    assert limited.decision is GateDecision.INTERACTIVE_CHALLENGE
    assert limited.reason_code == "RATE_LIMIT"
    assert tarpit.decision is GateDecision.TARPIT
    assert tarpit.reason_code == "RATE_BURST"


def test_missing_clearance_uses_pow_then_escalates_to_interactive() -> None:
    gate = GateEngine()

    first = gate.decide(
        path="/api/jobs",
        requests=[],
        ja3=None,
        ja4=None,
        clearance_valid=False,
        pow_failures=0,
    )
    escalated = gate.decide(
        path="/api/jobs",
        requests=[],
        ja3=None,
        ja4=None,
        clearance_valid=False,
        pow_failures=1,
    )

    assert first.decision is GateDecision.JS_CHALLENGE
    assert first.reason_code == "CLEARANCE_MISSING"
    assert escalated.decision is GateDecision.INTERACTIVE_CHALLENGE
    assert escalated.reason_code == "CHALLENGE_ESCALATION"


def test_live_risk_overrides_valid_clearance() -> None:
    gate = GateEngine()

    denied = gate.decide(
        path="/jobs",
        requests=[],
        ja3=None,
        ja4=None,
        clearance_valid=True,
        pow_failures=0,
        hard_risk_codes=("JS_HEADLESS_UA",),
    )
    challenged = gate.decide(
        path="/jobs",
        requests=[],
        ja3=None,
        ja4=None,
        clearance_valid=True,
        pow_failures=0,
        medium_risk_codes=("JS_OUTER_DIMENSIONS_ZERO", "TLS_SESSION_PERSONA_DRIFT"),
    )

    assert denied.decision is GateDecision.DENY
    assert denied.reason_code == "LIVE_HARD_RISK"
    assert challenged.decision is GateDecision.INTERACTIVE_CHALLENGE
    assert challenged.reason_code == "LIVE_MEDIUM_RISK"
