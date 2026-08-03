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
        (FailureKind.SERVER_ERROR, TransitionAction.RETRY_SAME_ROUTE),
        (FailureKind.PARSER_ERROR, TransitionAction.CHANGE_EXTRACTOR),
        (FailureKind.PARSE_EMPTY, TransitionAction.CHANGE_EXTRACTOR),
        (FailureKind.DNS_ERROR, TransitionAction.DEBOUNCED_PROXY),
        (FailureKind.AUTH_REQUIRED, TransitionAction.TERMINAL),
        (FailureKind.BOARD_GONE, TransitionAction.TERMINAL),
        (FailureKind.OK, TransitionAction.NONE),
    ],
)
def test_signal_transition_matrix(kind: FailureKind, action: TransitionAction) -> None:
    assert TransitionPolicy().decide(kind).action is action


def test_fingerprint_rejection_is_the_only_engine_switch_in_policy_table() -> None:
    decision = TransitionPolicy().decide(FailureKind.BLOCKED_FINGERPRINT)
    assert not decision.preserves_engine
    assert not decision.preserves_session
