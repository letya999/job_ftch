from __future__ import annotations

import pytest

from job_ftch.infrastructure.bypass.failure_signal import FailureKind
from job_ftch.infrastructure.bypass.transition_policy import (
    TransitionAction,
    TransitionPolicy,
)


@pytest.mark.parametrize(
    ("kind", "action"),
    [
        (FailureKind.CAPTCHA, TransitionAction.SOLVE_CURRENT_SESSION),
        (FailureKind.CHALLENGE, TransitionAction.SOLVE_CURRENT_SESSION),
        (FailureKind.RATE_LIMIT, TransitionAction.ACTIVATE_PROXY),
        (FailureKind.BLOCKED_IP, TransitionAction.ACTIVATE_PROXY_THEN_FALLBACK),
        (FailureKind.QRATOR_CHALLENGE, TransitionAction.ACTIVATE_PROXY_THEN_FALLBACK),
        (FailureKind.TLS_ERROR, TransitionAction.TLS_IMPERSONATION),
        (
            FailureKind.BLOCKED_FINGERPRINT,
            TransitionAction.FINGERPRINT_RESISTANT_ENGINE,
        ),
        (
            FailureKind.BLOCKED_CHROMIUM_FINGERPRINT,
            TransitionAction.ENGINE_DIVERSITY,
        ),
        (FailureKind.SERVER_ERROR, TransitionAction.RETRY_SAME_ROUTE),
        (FailureKind.PARSER_ERROR, TransitionAction.CHANGE_EXTRACTOR),
        (FailureKind.PARSE_EMPTY, TransitionAction.CHANGE_EXTRACTOR),
        (FailureKind.DNS_ERROR, TransitionAction.DEBOUNCED_PROXY),
        (FailureKind.AUTH_REQUIRED, TransitionAction.TERMINAL),
        (FailureKind.BOARD_GONE, TransitionAction.TERMINAL),
        (FailureKind.SILENT_BLOCK, TransitionAction.FINGERPRINT_RESISTANT_ENGINE),
        (FailureKind.PAYMENT_REQUIRED, TransitionAction.TERMINAL),
        (FailureKind.OK, TransitionAction.NONE),
    ],
)
def test_signal_transition_matrix(kind: FailureKind, action: TransitionAction) -> None:
    assert TransitionPolicy().decide(kind).action is action


@pytest.mark.parametrize("kind", [FailureKind.BLOCKED_FINGERPRINT, FailureKind.SILENT_BLOCK])
def test_engine_switching_kinds_drop_engine_and_session(kind: FailureKind) -> None:
    # Both a hard fingerprint block and a silent (200-shell) block can only be
    # cleared by a different engine on a fresh session.
    decision = TransitionPolicy().decide(kind)
    assert not decision.preserves_engine
    assert not decision.preserves_session


def test_every_failure_kind_has_an_explicit_decision() -> None:
    # OK is not a failure and UNKNOWN is deliberately special-cased in the
    # controller (recorded + threshold escalate); every other kind must have an
    # explicit table entry so a new FailureKind cannot ship as a silent NONE.
    from job_ftch.infrastructure.bypass.transition_policy import _DECISIONS

    special_cased = {FailureKind.OK, FailureKind.UNKNOWN}
    missing = [kind for kind in FailureKind if kind not in special_cased and kind not in _DECISIONS]
    assert not missing, f"FailureKind(s) without a transition decision: {missing}"
