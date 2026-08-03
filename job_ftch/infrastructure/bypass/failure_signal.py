"""Heuristic FailureSignal classifier (ADR-037).

Single source of truth for classifying fetch outcomes. Absorbs detection
logic previously scattered across site_fingerprinter._looks_like_challenge,
career_site._is_soft_403_error/_is_empty_200_response/_is_retryable_http_error,
and career_site_source._try_escalate_bypass legacy or-chain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping


class FailureKind(StrEnum):
    OK = "ok"
    RATE_LIMIT = "rate_limit"
    CAPTCHA = "captcha"
    CHALLENGE = "challenge"
    BLOCKED = "blocked"
    BLOCKED_IP = "blocked_ip"
    BLOCKED_FINGERPRINT = "blocked_fingerprint"
    AUTH_REQUIRED = "auth_required"
    TIMEOUT = "timeout"
    DNS_ERROR = "dns_error"
    CONNECT_ERROR = "connect_error"
    TLS_ERROR = "tls_error"
    PARSER_ERROR = "parser_error"
    PARSE_EMPTY = "parse_empty"
    SERVER_ERROR = "server_error"
    BOARD_GONE = "board_gone"
    DEADLINE = "deadline"
    UNKNOWN = "unknown"
    PAYMENT_REQUIRED = "payment_required"
    SILENT_BLOCK = "silent_block"


@runtime_checkable
class FailureSignal(Protocol):
    """Classify a fetch failure into a FailureKind."""

    def classify(
        self,
        *,
        status_code: int | None,
        headers: Mapping[str, str] | None,
        body: bytes | None,
        error: BaseException | None,
    ) -> FailureKind: ...


_CAPTCHA_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"не робот|не являетесь роботом", re.IGNORECASE),
    re.compile(r"hcaptcha|g-recaptcha|recaptcha|smartcaptcha|showcaptcha", re.IGNORECASE),
)

_PASSIVE_CHALLENGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"checking your browser", re.IGNORECASE),
    re.compile(r"just a moment", re.IGNORECASE),
)

_EMBEDDABLE_CAPTCHA_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"cloudflare", re.IGNORECASE),
    re.compile(r"datadome", re.IGNORECASE),
    re.compile(r"kasada", re.IGNORECASE),
    re.compile(r"perimeterx", re.IGNORECASE),
    re.compile(r"incapsula", re.IGNORECASE),
    re.compile(r"shape security", re.IGNORECASE),
    re.compile(r"f5 networks", re.IGNORECASE),
    re.compile(r"ddos-guard", re.IGNORECASE),
    re.compile(r"enable javascript", re.IGNORECASE),
    re.compile(r"hcaptcha", re.IGNORECASE),
    re.compile(r"recaptcha", re.IGNORECASE),
    re.compile(r"turnstile", re.IGNORECASE),
)

_CHALLENGE_MARKERS: tuple[str, ...] = (
    "cf-chl",
    "cf_chl_opt",
    "_cf_chl",
    "challenge-platform",
    "datadome",
    "dd=",
    "hcaptcha",
    "g-recaptcha",
    "smartcaptcha",
    "showcaptcha",
    "px-captcha",
)

_IP_BLOCK_MARKERS: tuple[str, ...] = (
    "ip has been blocked",
    "ip address has been blocked",
    "access from your ip",
    "too many requests from this ip",
    "proxy detected",
)

_FINGERPRINT_BLOCK_MARKERS: tuple[str, ...] = (
    "browser fingerprint",
    "unsupported browser configuration",
    "automated browser",
    "automation detected",
    "webdriver",
)

_TIMEOUT_ERROR_MARKERS: tuple[str, ...] = (
    "timeout",
    "navigation failed",
    "err_aborted",
    "err_http2_protocol_error",
    "err_connection_closed",
    "err_connection_reset",
    "err_empty_response",
    "err_connection_refused",
    "connection refused",
    "all connection attempts failed",
)

_BLOCKED_STATUSES: frozenset[int] = frozenset({401, 403})
_RATE_LIMIT_STATUSES: frozenset[int] = frozenset({429})
_SOFT_RETRY_STATUSES: frozenset[int] = frozenset({401, 403})
_VENDOR_BLOCKED_STATUSES: frozenset[int] = frozenset({498, 499})


@dataclass(frozen=True, slots=True)
class FetchOutcome:
    """Typed result of classifying a fetch attempt."""

    kind: FailureKind
    challenge: bool = False
    empty: bool = False
    retry_after_seconds: float | None = None
    captcha_type: str | None = None

    @property
    def retryable(self) -> bool:
        return self.kind in {
            FailureKind.RATE_LIMIT,
            FailureKind.SERVER_ERROR,
            FailureKind.TIMEOUT,
            FailureKind.DNS_ERROR,
            FailureKind.CONNECT_ERROR,
            FailureKind.TLS_ERROR,
        }

    @property
    def should_escalate(self) -> bool:
        return self.kind in {
            FailureKind.CAPTCHA,
            FailureKind.BLOCKED,
            FailureKind.BLOCKED_IP,
            FailureKind.BLOCKED_FINGERPRINT,
            FailureKind.CHALLENGE,
            FailureKind.TIMEOUT,
            FailureKind.RATE_LIMIT,
            FailureKind.TLS_ERROR,
            FailureKind.SILENT_BLOCK,
        }

    @property
    def is_soft_403(self) -> bool:
        return self.kind == FailureKind.BLOCKED


def is_challenge_body(text: str) -> bool:
    """Check if HTML body contains anti-bot challenge markers."""
    lowered = text.lower()
    return any(marker in lowered for marker in _CHALLENGE_MARKERS)


def _detect_captcha_type(text: str, headers: Mapping[str, str] | None = None) -> str | None:
    """Return a conservative label for observed challenge evidence."""
    lowered = text.lower()
    lowered_headers = {
        str(key).lower(): str(value).lower() for key, value in (headers or {}).items()
    }
    if "captcha" in lowered_headers.get("x-datadome", "") or "datadome" in lowered:
        return "datadome"
    if "px-captcha" in lowered or "perimeterx" in lowered:
        return "perimeterx"
    if "kasada" in lowered:
        return "kasada"
    if "incapsula" in lowered or "imperva" in lowered:
        return "incapsula"
    if "ddos-guard" in lowered:
        return "ddos_guard"
    if "smartcaptcha" in lowered or "showcaptcha" in lowered:
        return "smartcaptcha"
    if "hcaptcha" in lowered:
        return "hcaptcha"
    if "recaptcha/api.js" in lowered and "render=" in lowered:
        return "recaptcha_v3"
    if "recaptcha" in lowered or "g-recaptcha" in lowered:
        return "recaptcha"
    if "turnstile" in lowered or "cf-turnstile" in lowered:
        return "cloudflare_turnstile"
    if (
        lowered_headers.get("cf-mitigated") == "challenge"
        or "challenge-platform" in lowered
        or "cf-chl" in lowered
        or "cloudflare" in lowered
    ):
        return "cloudflare_challenge"
    if "geetest" in lowered:
        return "geetest"
    if "funcaptcha" in lowered or "arkose" in lowered:
        return "arkose_funcaptcha"
    if "aws waf" in lowered or "amazon captcha" in lowered:
        return "aws_waf"
    if "captcha" in lowered or "не робот" in lowered or "не являетесь роботом" in lowered:
        return "generic_captcha"
    return None


def _has_substantial_visible_content(text: str) -> bool:
    without_code = re.sub(
        r"<\s*(?:script|style)\b[^>]*>.*?<\s*/\s*(?:script|style)\b[^>]*>",
        " ",
        text,
        flags=re.I | re.S,
    )
    visible = " ".join(re.sub(r"<[^>]+>", " ", without_code).split())
    return len(visible) >= 200


def is_empty_html_200(status_code: int, content_type: str, body: str) -> bool:
    """Check if a 200 response is an empty HTML shell (anti-bot signal)."""
    if status_code != 200:
        return False
    if "text/html" not in content_type.lower():
        return False
    return not body.strip()


def classify_error(error: BaseException) -> FailureKind:
    """Classify an exception into a FailureKind without needing status/body."""
    err_name = type(error).__name__.lower()
    err_text = str(error).lower()
    if "timeout" in err_name or "timed out" in err_text or err_text == "timeout":
        return FailureKind.TIMEOUT
    tls_markers = (
        "certificate verify",
        "certificate_verify_failed",
        "sslerror",
        "ssl error",
        "tls error",
        "wrong version number",
        "unknown ca",
    )
    if "ssl" in err_name or any(marker in err_text for marker in tls_markers):
        return FailureKind.TLS_ERROR
    dns_markers = (
        "name or service not known",
        "nodename nor servname",
        "temporary failure in name resolution",
        "getaddrinfo failed",
        "no such host",
    )
    if "gaierror" in err_name or any(marker in err_text for marker in dns_markers):
        return FailureKind.DNS_ERROR
    connect_markers = (
        "connection refused",
        "connecterror",
        "connection reset",
        "connection closed",
        "all connection attempts failed",
        "err_connection_refused",
        "err_connection_reset",
        "err_connection_closed",
    )
    if (
        "connect" in err_name
        or "connection" in err_name
        or any(marker in err_text for marker in connect_markers)
    ):
        return FailureKind.CONNECT_ERROR
    for marker in _TIMEOUT_ERROR_MARKERS:
        if marker in err_text:
            return FailureKind.TIMEOUT
    if isinstance(error, ValueError):
        return FailureKind.PARSER_ERROR
    if "blocked" in err_text:
        return FailureKind.BLOCKED
    if "401" in err_text or "403" in err_text:
        return FailureKind.BLOCKED
    if "429" in err_text:
        return FailureKind.RATE_LIMIT
    if "503" in err_text or "502" in err_text or "500" in err_text:
        return FailureKind.SERVER_ERROR
    return FailureKind.UNKNOWN


@dataclass(slots=True)
class HeuristicFailureSignal:
    """Default FailureSignal. Maps status codes + body patterns to a kind."""

    def classify(
        self,
        *,
        status_code: int | None,
        headers: Mapping[str, str] | None = None,
        body: bytes | None,
        error: BaseException | None,
    ) -> FailureKind:
        return self.classify_detailed(
            status_code=status_code,
            headers=headers,
            body=body,
            error=error,
        ).kind

    def classify_detailed(
        self,
        *,
        status_code: int | None,
        headers: Mapping[str, str] | None = None,
        body: bytes | None,
        error: BaseException | None,
    ) -> FetchOutcome:
        if error is not None:
            kind = classify_error(error)
            if kind != FailureKind.UNKNOWN:
                return FetchOutcome(kind=kind)
        if status_code is None:
            return FetchOutcome(kind=FailureKind.UNKNOWN)

        text = ""
        if body is not None:
            try:
                text = body.decode("utf-8", errors="ignore")
            except Exception:
                text = ""

        lowered_headers = {
            str(key).lower(): str(value).lower() for key, value in (headers or {}).items()
        }
        if lowered_headers.get("cf-mitigated") == "challenge":
            return FetchOutcome(
                kind=FailureKind.CHALLENGE,
                challenge=True,
                captcha_type="cloudflare_challenge",
            )
        if "captcha" in lowered_headers.get("x-datadome", ""):
            return FetchOutcome(kind=FailureKind.CAPTCHA, challenge=True, captcha_type="datadome")

        # Challenge evidence is stronger than a status-only classification. A
        # large real page may legitimately embed a CAPTCHA vendor script, so
        # retain the substantial-content guard used for successful responses.
        if text:
            lowered = text.lower()
            substantial_content = _has_substantial_visible_content(text)
            if any(marker in lowered for marker in _IP_BLOCK_MARKERS):
                return FetchOutcome(kind=FailureKind.BLOCKED_IP)
            if any(marker in lowered for marker in _FINGERPRINT_BLOCK_MARKERS):
                return FetchOutcome(kind=FailureKind.BLOCKED_FINGERPRINT)
            for pattern in _CAPTCHA_PATTERNS:
                if pattern.search(text) and not substantial_content:
                    return FetchOutcome(
                        kind=FailureKind.CAPTCHA,
                        challenge=True,
                        captcha_type=_detect_captcha_type(text, lowered_headers),
                    )
            for pattern in _PASSIVE_CHALLENGE_PATTERNS:
                if pattern.search(text) and not substantial_content:
                    return FetchOutcome(
                        kind=FailureKind.CHALLENGE,
                        challenge=True,
                        captcha_type=_detect_captcha_type(text, lowered_headers),
                    )
            if not substantial_content and any(
                pattern.search(text) for pattern in _EMBEDDABLE_CAPTCHA_PATTERNS
            ):
                return FetchOutcome(
                    kind=FailureKind.CHALLENGE,
                    challenge=True,
                    captcha_type=_detect_captcha_type(text, lowered_headers),
                )
            if is_challenge_body(text) and not substantial_content:
                return FetchOutcome(
                    kind=FailureKind.CHALLENGE,
                    challenge=True,
                    captcha_type=_detect_captcha_type(text, lowered_headers),
                )

        if status_code == 402:
            return FetchOutcome(kind=FailureKind.PAYMENT_REQUIRED)
        if status_code == 401:
            return FetchOutcome(kind=FailureKind.AUTH_REQUIRED)
        if status_code in _BLOCKED_STATUSES or status_code in _VENDOR_BLOCKED_STATUSES:
            return FetchOutcome(kind=FailureKind.BLOCKED)
        if status_code in _RATE_LIMIT_STATUSES:
            retry_after_seconds = None
            if "retry-after" in lowered_headers:
                ra = lowered_headers["retry-after"]
                try:
                    retry_after_seconds = float(ra)
                except ValueError:
                    import email.utils
                    from datetime import datetime

                    try:
                        parsed = email.utils.parsedate_to_datetime(ra)
                        if parsed.tzinfo is None:
                            parsed = parsed.replace(tzinfo=UTC)
                        now = datetime.now(UTC)
                        retry_after_seconds = max(0.0, (parsed - now).total_seconds())
                    except (TypeError, ValueError):
                        pass
            return FetchOutcome(
                kind=FailureKind.RATE_LIMIT, retry_after_seconds=retry_after_seconds
            )
        if status_code >= 500:
            return FetchOutcome(kind=FailureKind.SERVER_ERROR)
        if status_code in {404, 410}:
            return FetchOutcome(kind=FailureKind.BOARD_GONE)
        if 200 <= status_code < 300:
            if body is None:
                return FetchOutcome(kind=FailureKind.OK)
            if not text.strip():
                return FetchOutcome(
                    kind=FailureKind.PARSE_EMPTY,
                    empty=True,
                )
            return FetchOutcome(kind=FailureKind.OK)
        return FetchOutcome(kind=FailureKind.UNKNOWN)


def classify_silent_block(
    outcome: FetchOutcome,
    *,
    expected_nonempty: bool,
    item_count: int,
) -> FetchOutcome:
    """Upgrade a parser-empty outcome to ``silent_block`` when caller evidence
    says the listing was non-empty.

    Sites with silent scoring (reCAPTCHA v3, Akamai Bot Manager, Cloudflare
    pass-through challenge) often return the substantive HTML shell with
    embedded detection scripts but strip the actual listing payload — the HTTP
    classifier sees a 2xx with substantial content and reports ``OK``. The
    scraper then finds zero job cards and ``OK`` stays as ``PARSE_EMPTY``.

    This helper lets the scraper layer (career_site / monitor) flag the
    outcome as a silent block when it knows the listing URL is supposed to
    be non-empty (e.g. ATS root search, sitemap-known lists). ``silent_block``
    is in ``should_escalate`` so the route controller switches route instead
    of retrying the same parser chain.
    """
    if not expected_nonempty or item_count > 0:
        return outcome
    if outcome.kind not in {FailureKind.OK, FailureKind.PARSE_EMPTY}:
        return outcome
    return FetchOutcome(
        kind=FailureKind.SILENT_BLOCK,
        empty=True,
        retry_after_seconds=outcome.retry_after_seconds,
        captcha_type=outcome.captcha_type,
    )
