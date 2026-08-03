"""Pure, table-driven transition decisions for the adaptive route graph."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from job_ftch.infrastructure.bypass.failure_signal import FailureKind


class TransitionAction(StrEnum):
    """Controller action selected from classified failure evidence."""

    NONE = "none"
    SOLVE_CURRENT_SESSION = "solve_current_session"
    ACTIVATE_PROXY = "activate_proxy"
    ACTIVATE_PROXY_THEN_FALLBACK = "activate_proxy_then_fallback"
    TLS_IMPERSONATION = "tls_impersonation"
    FINGERPRINT_RESISTANT_ENGINE = "fingerprint_resistant_engine"
    DEBOUNCED_PROXY = "debounced_proxy"
    RETRY_SAME_ROUTE = "retry_same_route"
    CHANGE_EXTRACTOR = "change_extractor"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class TransitionDecision:
    action: TransitionAction
    preserves_engine: bool = True
    preserves_session: bool = True


_DECISIONS: dict[FailureKind, TransitionDecision] = {
    FailureKind.CAPTCHA: TransitionDecision(TransitionAction.SOLVE_CURRENT_SESSION),
    FailureKind.CHALLENGE: TransitionDecision(TransitionAction.SOLVE_CURRENT_SESSION),
    FailureKind.QRATOR_CHALLENGE: TransitionDecision(
        TransitionAction.ACTIVATE_PROXY_THEN_FALLBACK,
        preserves_session=False,
    ),
    FailureKind.RATE_LIMIT: TransitionDecision(TransitionAction.ACTIVATE_PROXY),
    FailureKind.BLOCKED_IP: TransitionDecision(TransitionAction.ACTIVATE_PROXY_THEN_FALLBACK),
    FailureKind.BLOCKED: TransitionDecision(TransitionAction.ACTIVATE_PROXY_THEN_FALLBACK),
    FailureKind.TLS_ERROR: TransitionDecision(TransitionAction.TLS_IMPERSONATION),
    FailureKind.BLOCKED_FINGERPRINT: TransitionDecision(
        TransitionAction.FINGERPRINT_RESISTANT_ENGINE,
        preserves_engine=False,
        preserves_session=False,
    ),
    # Silent scoring (reCAPTCHA v3 / Akamai / Cloudflare pass-through) returns a
    # full 200 shell with the listing stripped. Retrying the same parser chain
    # is useless; the only lever is a fingerprint-resistant engine on a fresh
    # session, so treat it like a fingerprint block (defect B1).
    FailureKind.SILENT_BLOCK: TransitionDecision(
        TransitionAction.FINGERPRINT_RESISTANT_ENGINE,
        preserves_engine=False,
        preserves_session=False,
    ),
    # A hard paywall is not anti-bot evidence and no route change can clear it.
    FailureKind.PAYMENT_REQUIRED: TransitionDecision(TransitionAction.TERMINAL),
    FailureKind.TIMEOUT: TransitionDecision(TransitionAction.DEBOUNCED_PROXY),
    FailureKind.DNS_ERROR: TransitionDecision(TransitionAction.DEBOUNCED_PROXY),
    FailureKind.CONNECT_ERROR: TransitionDecision(TransitionAction.DEBOUNCED_PROXY),
    FailureKind.SERVER_ERROR: TransitionDecision(TransitionAction.RETRY_SAME_ROUTE),
    FailureKind.PARSER_ERROR: TransitionDecision(TransitionAction.CHANGE_EXTRACTOR),
    FailureKind.PARSE_EMPTY: TransitionDecision(TransitionAction.CHANGE_EXTRACTOR),
    FailureKind.AUTH_REQUIRED: TransitionDecision(TransitionAction.TERMINAL),
    FailureKind.BOARD_GONE: TransitionDecision(TransitionAction.TERMINAL),
    FailureKind.DEADLINE: TransitionDecision(TransitionAction.TERMINAL),
}


class TransitionPolicy:
    """Map a typed failure to a unit-testable route-graph action."""

    def decide(self, kind: FailureKind) -> TransitionDecision:
        return _DECISIONS.get(kind, TransitionDecision(TransitionAction.NONE))
