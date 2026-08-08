"""Heuristic bot-detection classification examples."""

from __future__ import annotations

import pytest

from job_ftch.infrastructure.bypass.failure_signal import (
    FailureKind,
    FetchOutcome,
    HeuristicFailureSignal,
    _detect_captcha_type,
)


@pytest.mark.parametrize(
    ("status_code", "body", "error", "expected"),
    [
        (200, b"<div id='ddos-guard'>Checking your browser...</div>", None, FailureKind.CHALLENGE),
        (200, "Подтвердите, что вы не робот".encode(), None, FailureKind.CAPTCHA),
        (403, b"cf-browser-verification", None, FailureKind.BLOCKED),
        (429, b"Too Many Requests", None, FailureKind.RATE_LIMIT),
        (503, b"Service Unavailable", None, FailureKind.SERVER_ERROR),
        (200, b"", None, FailureKind.PARSE_EMPTY),
        (200, b'<div class="cf-turnstile"></div>', None, FailureKind.CHALLENGE),
        (
            200,
            b"himalayas.app Performing security verification. "
            b"This website uses a security service to protect against malicious bots. "
            b"Performance and Security by Cloudflare",
            None,
            FailureKind.CHALLENGE,
        ),
        (200, b"<iframe src='hcaptcha.com'></iframe>", None, FailureKind.CAPTCHA),
        (200, b"<script src='recaptcha/api.js'></script>", None, FailureKind.CAPTCHA),
        (200, b"<script src='smartcaptcha'></script>", None, FailureKind.CAPTCHA),
        (200, b"/showcaptcha?retpath=aHR0cHM6Ly9jYXJlZXIuY2lhbi5ydS8=", None, FailureKind.CAPTCHA),
        (
            200,
            b"<script>document.cookie='jsid=1';window.location.reload()</script>Qrator",
            None,
            FailureKind.QRATOR_CHALLENGE,
        ),
        (None, None, ConnectionError("timeout"), FailureKind.TIMEOUT),
        (402, b"Payment Required", None, FailureKind.PAYMENT_REQUIRED),
        (498, b"Anti-bot block", None, FailureKind.BLOCKED),
        (499, b"Client Closed Request", None, FailureKind.BLOCKED),
    ],
)
def test_failure_signal_classifies_challenge_and_transport_patterns(
    status_code, body, error, expected
) -> None:
    assert (
        HeuristicFailureSignal().classify(status_code=status_code, body=body, error=error)
        is expected
    )


@pytest.mark.parametrize("vendor", [b"recaptcha/api.js", b"cloudflare/turnstile.js"])
def test_failure_signal_does_not_reject_real_job_page_with_vendor_script(vendor: bytes) -> None:
    body = (
        b"<html><body><h1>Engineer</h1><p>"
        + (b"Build production systems. " * 18)
        + b"</p><script src='"
        + vendor
        + b"'></script></body></html>"
    )
    assert (
        HeuristicFailureSignal().classify(status_code=200, body=body, error=None) is FailureKind.OK
    )


def test_429_with_retry_after_header() -> None:
    outcome = HeuristicFailureSignal().classify_detailed(
        status_code=429,
        headers={"Retry-After": "120"},
        body=b"Too Many Requests",
        error=None,
    )
    assert outcome.kind is FailureKind.RATE_LIMIT
    assert outcome.retry_after_seconds == 120.0


def test_qrator_header_is_classified_as_qrator_challenge() -> None:
    outcome = HeuristicFailureSignal().classify_detailed(
        status_code=200,
        headers={"X-Qrator-RequestID": "fixture"},
        body=b"",
        error=None,
    )

    assert outcome.kind is FailureKind.QRATOR_CHALLENGE
    assert outcome.challenge is True
    assert outcome.captcha_type == "qrator_jsid"


def test_silent_block_triggers_escalation() -> None:
    outcome = FetchOutcome(kind=FailureKind.SILENT_BLOCK)
    assert outcome.should_escalate is True
    assert outcome.retryable is False


def test_payment_required_is_terminal() -> None:
    outcome = FetchOutcome(kind=FailureKind.PAYMENT_REQUIRED)
    assert outcome.should_escalate is False
    assert outcome.retryable is False


def test_turnstile_marker_is_normalized_for_solver_routes() -> None:
    assert _detect_captcha_type('<div class="cf-turnstile"></div>') == "turnstile"
