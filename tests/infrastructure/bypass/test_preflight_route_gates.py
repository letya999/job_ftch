from __future__ import annotations

from types import SimpleNamespace

import pytest

from job_ftch.application.registry import BypassCapability
from job_ftch.infrastructure.bypass import domain_intel, preflight, risk_router


@pytest.fixture
def cached_nodriver(monkeypatch: pytest.MonkeyPatch) -> None:
    intel = SimpleNamespace(
        get_recommended_route=lambda domain: ("nodriver", "direct"),
        get=lambda domain: SimpleNamespace(known_vendor=""),
    )
    monkeypatch.setattr(domain_intel, "get_domain_intel", lambda: intel)
    monkeypatch.setattr(
        preflight,
        "get_bypass_capability",
        lambda name: BypassCapability(legal_gate="adr_073"),
    )
    monkeypatch.setattr(
        risk_router,
        "get_router",
        lambda: SimpleNamespace(select_tier=lambda url: "noop"),
    )


def test_cached_route_cannot_bypass_current_legal_gate(
    monkeypatch: pytest.MonkeyPatch,
    cached_nodriver: None,
) -> None:
    del cached_nodriver
    monkeypatch.setattr(preflight, "resolve_bypass", lambda name, config=None: object())
    result = preflight.run_preflight(
        "https://company.invalid/jobs",
        config={"allow_adr_073": False},
    )
    assert result.tier == "noop"
    assert not result.reason.startswith("domain_intel_cache")


def test_unavailable_cached_engine_is_invalidated_for_this_run(
    monkeypatch: pytest.MonkeyPatch,
    cached_nodriver: None,
) -> None:
    del cached_nodriver

    def _resolve(name: str, config=None):
        del config
        if name == "nodriver":
            raise ValueError("not installed")
        return object()

    monkeypatch.setattr(preflight, "resolve_bypass", _resolve)
    result = preflight.run_preflight("https://company.invalid/jobs")
    assert result.tier == "noop"
