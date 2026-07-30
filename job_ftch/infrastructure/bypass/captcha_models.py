"""Typed CAPTCHA orchestration outcomes shared by solvers and providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class CaptchaFailureReason(StrEnum):
    PROVIDER_DISABLED = "provider_disabled"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    UNSUPPORTED_CHALLENGE = "unsupported_challenge"
    MISSING_CREDENTIAL = "missing_credential"
    BUDGET_EXHAUSTED = "budget_exhausted"
    DEADLINE_INSUFFICIENT = "deadline_insufficient"
    PROVIDER_REJECTED = "provider_rejected"
    PROVIDER_TIMEOUT = "provider_timeout"
    BAD_TOKEN = "bad_token"
    INJECTION_FAILED = "injection_failed"
    VERIFICATION_FAILED = "verification_failed"


@dataclass(slots=True)
class CaptchaSolveResult:
    solved: bool
    method: str
    cookies: dict[str, str] = field(default_factory=dict)
    tokens: dict[str, str] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    error: str | None = None
    failure_reason: CaptchaFailureReason | None = None
