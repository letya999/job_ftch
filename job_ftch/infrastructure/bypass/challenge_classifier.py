"""Unified anti-bot/challenge classification for HTTP, monitor and browser paths."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from job_ftch.infrastructure.bypass.failure_signal import (
    FailureKind,
    HeuristicFailureSignal,
)

logger = structlog.get_logger("job_ftch.bypass.challenge")

if TYPE_CHECKING:
    from collections.abc import Mapping

_CHALLENGE_KINDS = {
    FailureKind.CAPTCHA,
    FailureKind.CHALLENGE,
    FailureKind.QRATOR_CHALLENGE,
    FailureKind.BLOCKED,
    FailureKind.BLOCKED_IP,
    FailureKind.BLOCKED_FINGERPRINT,
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
) -> ChallengeDetection:
    body_bytes = _body_bytes(body)
    outcome = HeuristicFailureSignal().classify_detailed(
        status_code=status_code,
        headers=headers,
        body=body_bytes,
        error=None,
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
    """Emit token/cookie-safe challenge telemetry."""
    if not detection.detected:
        return
    logger.info(
        "challenge_detected",
        domain=domain,
        type=detection.challenge_type or detection.kind.value,
        confidence=detection.confidence,
        surface=detection.surface,
        status_code=detection.status_code,
        latency_ms=round(detection.latency_ms, 1) if detection.latency_ms is not None else None,
        evidence_hash=detection.evidence_hash,
    )


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
