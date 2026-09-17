"""Unified anti-bot/challenge classification for HTTP, monitor and browser paths."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from job_ftch.infrastructure.bypass.failure_signal import (
    FailureKind,
    HeuristicFailureSignal,
)

logger = structlog.get_logger("job_ftch.bypass.challenge")

if TYPE_CHECKING:
    from collections.abc import Mapping

_counter: Any = None

_CHALLENGE_KINDS = {
    FailureKind.CAPTCHA,
    FailureKind.CHALLENGE,
    FailureKind.QRATOR_CHALLENGE,
    FailureKind.BLOCKED_IP,
    FailureKind.BLOCKED_FINGERPRINT,
    FailureKind.BLOCKED_CHROMIUM_FINGERPRINT,
}

_CAPTCHA_ENCOUNTER_KINDS = {
    FailureKind.CAPTCHA,
    FailureKind.CHALLENGE,
    FailureKind.QRATOR_CHALLENGE,
}


@dataclass(frozen=True, slots=True)
class ChallengeDetection:
    detected: bool
    kind: FailureKind
    challenge_type: str | None
    confidence: float
    surface: str
    status_code: int | None
    evidence_hash: str
    latency_ms: float | None = None


def classify_challenge(
    *,
    surface: str,
    status_code: int | None = None,
    headers: Mapping[str, str] | None = None,
    body: bytes | str | None = None,
    started_at: float | None = None,
    page_url: str | None = None,
) -> ChallengeDetection:
    body_bytes = _body_bytes(body)
    outcome = HeuristicFailureSignal().classify_detailed(
        status_code=status_code,
        headers=headers,
        body=body_bytes,
        error=None,
        page_url=page_url,
    )
    detected = outcome.kind in _CHALLENGE_KINDS
    return ChallengeDetection(
        detected=detected,
        kind=outcome.kind,
        challenge_type=outcome.captcha_type,
        confidence=_confidence(outcome.kind, bool(outcome.captcha_type), status_code),
        surface=surface,
        status_code=status_code,
        evidence_hash=_evidence_hash(headers, body_bytes),
        latency_ms=((time.monotonic() - started_at) * 1000.0) if started_at else None,
    )


def emit_challenge_detection(domain: str, detection: ChallengeDetection) -> None:
    """Emit a first-class OpenObserve CAPTCHA encounter (token/cookie-safe)."""
    if not detection.detected or detection.kind not in _CAPTCHA_ENCOUNTER_KINDS:
        return
    emit_captcha_event(
        event="captcha_encounter",
        captcha_host=domain,
        captcha_type=detection.challenge_type or detection.kind.value,
        captcha_kind=detection.kind.value,
        captcha_surface=detection.surface,
        captcha_outcome="observed",
        captcha_solved=False,
        status_code=detection.status_code,
        evidence_hash=detection.evidence_hash,
        latency_ms=round(detection.latency_ms, 1) if detection.latency_ms is not None else None,
    )


def emit_captcha_solve_outcome(
    *,
    host: str,
    captcha_type: str | None,
    solved: bool,
    result_kind: str | None = None,
    failure_reason: str | None = None,
    engine: str | None = None,
    provider: str | None = None,
    source_url: str | None = None,
) -> None:
    """Record whether a detected challenge was solved or left unsolved."""
    if solved:
        outcome = "solved"
    elif result_kind == "unsupported" or failure_reason in {
        "unsupported_challenge",
        "unauthorized_domain",
        "provider_disabled",
    }:
        outcome = "unsupported"
    else:
        outcome = "failed"
    emit_captcha_event(
        event="captcha_solve_outcome",
        captcha_host=host,
        captcha_type=captcha_type or "unknown",
        captcha_outcome=outcome,
        captcha_solved=solved,
        captcha_result_kind=result_kind,
        captcha_failure_reason=failure_reason,
        captcha_engine=engine,
        captcha_provider=provider,
        source_url=source_url,
    )


def emit_captcha_event(event: str, **fields: Any) -> None:
    """One searchable log row plus an OTel counter for CAPTCHA telemetry."""
    payload = {key: value for key, value in fields.items() if value is not None}
    # Resolve against the current structlog configuration so test capture and
    # runtime reconfiguration cannot retain a stale bound logger.
    structlog.get_logger("job_ftch.bypass.challenge").info(event, **payload)
    try:
        counter = _captcha_metric()
        if counter is None:
            return
        counter.add(
            1,
            {
                "event": event,
                "captcha_type": str(payload.get("captcha_type") or "unknown"),
                "captcha_outcome": str(payload.get("captcha_outcome") or "observed"),
                "captcha_host": str(payload.get("captcha_host") or "unknown")[:120],
            },
        )
    except Exception:
        return


def _captcha_metric() -> Any:
    global _counter
    if _counter is False:
        return None
    if _counter is not None:
        return _counter
    try:
        from opentelemetry import metrics

        _counter = metrics.get_meter("job_ftch.bypass").create_counter(
            "job_ftch.bypass.captcha_events",
            description="CAPTCHA encounters and solve outcomes by type and host",
        )
    except Exception:
        _counter = False
        return None
    return _counter


def _body_bytes(body: bytes | str | None) -> bytes | None:
    if body is None:
        return None
    if isinstance(body, bytes):
        return body[:100_000]
    return body.encode("utf-8", errors="ignore")[:100_000]


def _evidence_hash(headers: Mapping[str, str] | None, body: bytes | None) -> str:
    digest = hashlib.sha256()
    for key, value in sorted((headers or {}).items()):
        digest.update(str(key).lower().encode("utf-8", errors="ignore"))
        digest.update(b":")
        digest.update(str(value).lower().encode("utf-8", errors="ignore")[:512])
        digest.update(b"\n")
    digest.update((body or b"")[:4096])
    return digest.hexdigest()[:16]


def _confidence(kind: FailureKind, has_type: bool, status_code: int | None) -> float:
    if kind is FailureKind.QRATOR_CHALLENGE:
        return 0.95
    if kind in {FailureKind.CAPTCHA, FailureKind.CHALLENGE}:
        return 0.92 if has_type else 0.82
    if kind in {FailureKind.BLOCKED_IP, FailureKind.BLOCKED_FINGERPRINT}:
        return 0.88
    if kind is FailureKind.BLOCKED and status_code in {403, 498, 499}:
        return 0.74
    if kind is FailureKind.BLOCKED:
        return 0.65
    return 0.0
