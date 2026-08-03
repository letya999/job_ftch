"""Typed CAPTCHA orchestration outcomes shared by solvers and providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class CaptchaChallengeType(StrEnum):
    RECAPTCHA = "recaptcha"
    RECAPTCHA_V3 = "recaptcha_v3"
    HCAPTCHA = "hcaptcha"
    TURNSTILE = "turnstile"
    CLOUDFLARE_CHALLENGE = "cloudflare_challenge"
    DATADOME = "datadome"
    PERIMETERX = "perimeterx"
    IMAGE = "image"
    UNKNOWN = "unknown"


class CaptchaResultKind(StrEnum):
    TOKEN = "token"
    SESSION = "session"
    MANUAL_REQUIRED = "manual_required"
    SKIPPED_OBSERVE = "skipped_observe"
    UNSUPPORTED = "unsupported"


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
    BACKOFF_ACTIVE = "backoff_active"
    UNAUTHORIZED_DOMAIN = "unauthorized_domain"


@dataclass(frozen=True, slots=True)
class CaptchaChallenge:
    challenge_type: str = CaptchaChallengeType.UNKNOWN.value
    page_url: str = ""
    site_key: str = ""
    action: str = ""
    enterprise: bool = False
    proxy_required: bool = False
    browser_context_required: bool = False
    confidence: float = 0.0
    raw_evidence: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CaptchaProviderCapability:
    provider: str
    supported_challenge_types: frozenset[str]
    result_kinds: frozenset[CaptchaResultKind]
    production_candidate: bool = False
    benchmark_candidate: bool = False
    free_or_dev: bool = False
    requires_api_key: bool = True
    requires_proxy: bool = False
    browser_context_required: bool = False
    notes: str = ""


@dataclass(slots=True)
class CaptchaSolveResult:
    solved: bool
    method: str
    cookies: dict[str, str] = field(default_factory=dict)
    tokens: dict[str, str] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    error: str | None = None
    failure_reason: CaptchaFailureReason | None = None
    challenge_type: str = CaptchaChallengeType.UNKNOWN.value
    result_kind: CaptchaResultKind | None = None
    provider_task_id: str = ""
    cost_estimate_usd: float | None = None
    raw_provider_status: str = ""
