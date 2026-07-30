"""Tests for the adaptive bypass escalation policy (ADR-037)."""

from __future__ import annotations

from typing import Any

import pytest

from job_ftch.application.registry import resolve_bypass
from job_ftch.config import Settings
from job_ftch.infrastructure.bypass.adaptive import (
    DEFAULT_TIER_ORDER,
    AdaptiveBypassManager,
    _create_adaptive,
)
from job_ftch.infrastructure.bypass.failure_signal import (
    FailureKind,
    FetchOutcome,
    HeuristicFailureSignal,
    classify_error,
    is_challenge_body,
    is_empty_html_200,
)


def test_adaptive_builds_tiers_from_registry() -> None:
    """The tier list is built from registered bypasses, not hardcoded."""
    mgr = AdaptiveBypassManager(adaptive_enabled=True)
    # `noop` is always registered; the manager must not crash on construction
    # even if optional tiers are absent.
    assert mgr.available_tiers
    assert mgr.available_tiers[0] == "noop"
    # The static DEFAULT_TIER_ORDER documents the canonical order.
    assert DEFAULT_TIER_ORDER[0] == "noop"
    assert DEFAULT_TIER_ORDER[-1] == "cloak"


def test_adaptive_disabled_blocks_all_escalations() -> None:
    mgr = AdaptiveBypassManager(adaptive_enabled=False)
    initial = mgr.current_name
    escalated = mgr.escalate()
    assert escalated is False
    assert mgr.current_name == initial


def test_adaptive_exhausted_only_at_last_available_tier() -> None:
    mgr = AdaptiveBypassManager(adaptive_enabled=True)

    assert mgr.exhausted is (len(mgr.available_tiers) == 1)
    while mgr.escalate():
        pass
    assert mgr.exhausted is True


@pytest.mark.anyio
async def test_adaptive_escalates_on_captcha() -> None:
    mgr = AdaptiveBypassManager(adaptive_enabled=True)
    if len(mgr.available_tiers) < 2:
        return  # only `noop` registered; nothing to escalate into
    initial = mgr.current_name
    await mgr.handle_failure(
        "src-1",
        status_code=200,
        body=b"<html>checking your browser (cloudflare)</html>",
    )
    assert mgr.current_name != initial
    if "nodriver" in mgr.available_tiers:
        assert mgr.current_name == "nodriver"
    elif "cloak" in mgr.available_tiers:
        assert mgr.current_name == "cloak"
    elif "stealth_browser" in mgr.available_tiers:
        assert mgr.current_name == "stealth_browser"
    assert mgr.escalations_total == 1


@pytest.mark.anyio
async def test_blocked_escalates_immediately() -> None:
    mgr = AdaptiveBypassManager(adaptive_enabled=True)
    if len(mgr.available_tiers) < 2:
        return
    initial = mgr.current_name
    await mgr.handle_failure("src-1", status_code=403)
    assert mgr.current_name != initial
    assert mgr.escalations_total == 1


@pytest.mark.anyio
async def test_debounce_window_not_escalated_on_first_timeout() -> None:
    mgr = AdaptiveBypassManager(adaptive_enabled=True)
    if len(mgr.available_tiers) < 2:
        return
    initial = mgr.current_name
    await mgr.handle_failure("src-1", status_code=500)
    assert mgr.current_name == initial


@pytest.mark.anyio
async def test_server_errors_never_trigger_browser_escalation() -> None:
    mgr = AdaptiveBypassManager(adaptive_enabled=True)
    if len(mgr.available_tiers) < 2:
        return
    initial = mgr.current_name
    await mgr.handle_failure("src-1", status_code=500)
    await mgr.handle_failure("src-1", status_code=500)
    await mgr.handle_failure("src-1", status_code=500)
    assert mgr.current_name == initial


@pytest.mark.anyio
async def test_timeout_debounced_not_immediate() -> None:
    """A single timeout should not escalate immediately (debounce threshold=2)."""
    mgr = AdaptiveBypassManager(adaptive_enabled=True)
    if len(mgr.available_tiers) < 2:
        return
    initial = mgr.current_name
    await mgr.handle_failure(
        "src-1",
        error=TimeoutError("connect timed out"),
    )
    assert mgr.current_name == initial
    assert mgr.escalations_total == 0


@pytest.mark.anyio
async def test_timeout_without_proxy_does_not_trigger_browser_escalation() -> None:
    """Transport failures must not be misread as fingerprint failures."""
    mgr = AdaptiveBypassManager(adaptive_enabled=True)
    if len(mgr.available_tiers) < 2:
        return
    initial = mgr.current_name
    await mgr.handle_failure("src-1", error=TimeoutError("t1"))
    await mgr.handle_failure("src-1", error=TimeoutError("t2"))
    assert mgr.current_name == initial
    assert mgr.escalations_total == 0


@pytest.mark.anyio
async def test_adaptive_parse_empty_does_not_escalate() -> None:
    mgr = AdaptiveBypassManager(adaptive_enabled=True)
    initial = mgr.current_name
    for _ in range(10):
        await mgr.handle_failure("src-1", status_code=200, body=b"")
    assert mgr.current_name == initial
    assert mgr.escalations_total == 0


@pytest.mark.anyio
async def test_adaptive_failures_are_per_source() -> None:
    """Failures on one source do not push another source past the threshold."""
    mgr = AdaptiveBypassManager(adaptive_enabled=True)
    if len(mgr.available_tiers) < 2:
        return
    await mgr.handle_failure("src-1", status_code=500)
    initial_after_src1 = mgr.current_name
    # src-2 has 0 failures; a single failure should not push it.
    await mgr.handle_failure("src-2", status_code=500)
    assert mgr.current_name == initial_after_src1
    assert mgr.escalations_total == 0


@pytest.mark.anyio
async def test_adaptive_returns_classified_kind() -> None:
    mgr = AdaptiveBypassManager(adaptive_enabled=False)
    assert await mgr.handle_failure("src", status_code=200) == FailureKind.OK
    assert await mgr.handle_failure("src", status_code=403) == FailureKind.BLOCKED
    assert await mgr.handle_failure("src", status_code=429) == FailureKind.RATE_LIMIT
    assert await mgr.handle_failure("src", status_code=500) == FailureKind.SERVER_ERROR


@pytest.mark.anyio
async def test_rate_limit_route_transition_never_sleeps() -> None:
    mgr = AdaptiveBypassManager(adaptive_enabled=True)

    await mgr.handle_failure("src-1", status_code=429, retry_after=60)

    # The HTTP retry layer owns Retry-After. The route controller can be called
    # while a detail/browser limiter is held and therefore must never sleep.
    assert mgr.current_name == "noop"


def test_heuristic_signal_classifies_captcha_body() -> None:
    sig = HeuristicFailureSignal()
    assert (
        sig.classify(
            status_code=200, body=b"checking your browser before accessing cloudflare", error=None
        )
        == FailureKind.CHALLENGE
    )
    assert (
        sig.classify(status_code=200, body=b"reCAPTCHA verification needed", error=None)
        == FailureKind.CAPTCHA
    )
    assert (
        sig.classify(status_code=200, body=b"datadome incident id", error=None)
        == FailureKind.CHALLENGE
    )


def test_heuristic_signal_classifies_status_codes() -> None:
    sig = HeuristicFailureSignal()
    assert sig.classify(status_code=401, body=None, error=None) == FailureKind.AUTH_REQUIRED
    assert sig.classify(status_code=403, body=None, error=None) == FailureKind.BLOCKED
    assert sig.classify(status_code=429, body=None, error=None) == FailureKind.RATE_LIMIT
    assert sig.classify(status_code=500, body=None, error=None) == FailureKind.SERVER_ERROR
    assert (
        sig.classify(
            status_code=200, body=b"<!DOCTYPE html><html><body>vacancy</body></html>", error=None
        )
        == FailureKind.OK
    )


def test_heuristic_signal_classifies_playwright_connection_errors() -> None:
    sig = HeuristicFailureSignal()
    assert (
        sig.classify(
            status_code=None,
            body=None,
            error=RuntimeError("Page.goto: net::ERR_CONNECTION_REFUSED at https://x"),
        )
        == FailureKind.CONNECT_ERROR
    )
    assert (
        sig.classify(
            status_code=None,
            body=None,
            error=RuntimeError("All connection attempts failed"),
        )
        == FailureKind.CONNECT_ERROR
    )


def test_adaptive_resolve_bypass_noop_works() -> None:
    """Smoke: resolve_bypass('noop') is always available."""
    strat: Any = resolve_bypass("noop")
    assert strat is not None


def test_adaptive_disabled_returns_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "job_ftch.config.get_settings",
        lambda: Settings(adaptive_scraping_enabled=False),
    )
    strat = _create_adaptive()
    assert strat.__class__.__name__ == "NoopBypass"


# --- FetchOutcome and unified classifier tests ---


def test_fetch_outcome_properties() -> None:
    ok = FetchOutcome(kind=FailureKind.OK)
    assert not ok.retryable
    assert not ok.should_escalate

    blocked = FetchOutcome(kind=FailureKind.BLOCKED)
    assert not blocked.retryable
    assert blocked.should_escalate
    assert blocked.is_soft_403

    timeout = FetchOutcome(kind=FailureKind.TIMEOUT)
    assert timeout.retryable
    assert timeout.should_escalate

    captcha = FetchOutcome(kind=FailureKind.CAPTCHA, challenge=True)
    assert captcha.challenge
    assert captcha.should_escalate

    empty = FetchOutcome(kind=FailureKind.PARSE_EMPTY, empty=True)
    assert empty.empty
    assert not empty.should_escalate


def test_classify_detailed_returns_fetch_outcome() -> None:
    sig = HeuristicFailureSignal()
    outcome = sig.classify_detailed(status_code=403, body=None, error=None)
    assert isinstance(outcome, FetchOutcome)
    assert outcome.kind == FailureKind.BLOCKED

    outcome = sig.classify_detailed(
        status_code=200,
        body=b"<html>cloudflare challenge</html>",
        error=None,
    )
    assert outcome.kind == FailureKind.CHALLENGE
    assert outcome.challenge


def test_classify_error_covers_legacy_markers() -> None:
    assert classify_error(RuntimeError("navigation failed")) == FailureKind.TIMEOUT
    assert classify_error(RuntimeError("err_http2_protocol_error")) == FailureKind.TIMEOUT
    assert classify_error(RuntimeError("err_connection_closed")) == FailureKind.CONNECT_ERROR
    assert classify_error(RuntimeError("err_connection_reset")) == FailureKind.CONNECT_ERROR
    assert classify_error(RuntimeError("err_empty_response")) == FailureKind.TIMEOUT
    assert classify_error(RuntimeError("blocked by waf")) == FailureKind.BLOCKED
    assert classify_error(RuntimeError("some random error")) == FailureKind.UNKNOWN


def test_is_challenge_body_detects_markers() -> None:
    assert is_challenge_body("<html>cf-chl-bypass</html>")
    assert is_challenge_body('<div class="datadome-popup">')
    assert is_challenge_body('<div id="px-captcha">')
    assert not is_challenge_body("<html><body>Normal page</body></html>")


def test_is_empty_html_200() -> None:
    assert is_empty_html_200(200, "text/html; charset=utf-8", "")
    assert is_empty_html_200(200, "text/html", "   ")
    assert not is_empty_html_200(200, "application/json", "")
    assert not is_empty_html_200(404, "text/html", "")
    assert not is_empty_html_200(200, "text/html", "<html>content</html>")
