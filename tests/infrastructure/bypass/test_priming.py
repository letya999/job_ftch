"""TRACK C - background priming: freshness decisions, polite budgeted cycle,
sidecar persistence. No real browser is launched (the visit is injected)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from job_ftch.infrastructure.bypass.priming import (
    BackgroundPrimer,
    PrimingOutcome,
    PrimingState,
    _min_clearance_expiry,
    _normalize_domain,
)


def _settings(tmp_path: Path, **over: Any) -> SimpleNamespace:
    base = dict(
        bypass_background_priming_enabled=True,
        bypass_priming_state_dir=tmp_path / "priming",
        bypass_priming_refresh_window_seconds=600,
        bypass_priming_max_domains_per_cycle=20,
        bypass_priming_settle_seconds=0.0,
        bypass_priming_min_interval_seconds=1800,
        bypass_priming_prefetch_listings=False,
        bypass_default_requests_per_second=2.0,
    )
    base.update(over)
    return SimpleNamespace(**base)


class _FakePacer:
    def __init__(self) -> None:
        self.acquired: list[str] = []

    async def acquire(self, domain: str) -> float:
        self.acquired.append(domain)
        return 0.0


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class TestNormalizeAndExpiry:
    def test_normalize_domain(self) -> None:
        assert _normalize_domain("https://Jobs.Example.com/careers?x=1") == "jobs.example.com"
        assert _normalize_domain("jobs.example.com/list") == "jobs.example.com"
        assert _normalize_domain("  ") == ""

    def test_min_clearance_expiry_ignores_non_clearance_and_sessions(self) -> None:
        cookies = [
            {"name": "cf_clearance", "expires": 2000.0},
            {"name": "datadome", "expires": 1500.0},
            {"name": "cf_clearance", "expires": -1},  # session cookie ignored
            {"name": "other", "expires": 10.0},  # not clearance
        ]
        assert _min_clearance_expiry(cookies) == 1500.0
        assert _min_clearance_expiry([{"name": "x", "expires": 5}]) == 0.0


class TestFreshness:
    def test_cold_domain_is_a_miss(self, tmp_path: Path) -> None:
        primer = BackgroundPrimer(_settings(tmp_path), pacer=_FakePacer(), clock=_Clock(1000.0))
        decision = primer.needs_priming("cold.example.com")
        assert decision.should_prime is True
        assert decision.outcome is PrimingOutcome.PRIMED

    def test_fresh_domain_is_skipped(self, tmp_path: Path) -> None:
        clock = _Clock(1000.0)
        primer = BackgroundPrimer(_settings(tmp_path), pacer=_FakePacer(), clock=clock)
        primer._save_state(
            PrimingState(domain="warm.example.com", last_primed=900.0, clearance_expires=9999.0)
        )
        decision = primer.needs_priming("warm.example.com")
        assert decision.should_prime is False
        assert decision.outcome is PrimingOutcome.SKIPPED_WARM

    def test_near_expiry_refreshes(self, tmp_path: Path) -> None:
        clock = _Clock(1000.0)
        primer = BackgroundPrimer(_settings(tmp_path), pacer=_FakePacer(), clock=clock)
        # expiry within the 600s refresh window of now=1000 -> refresh
        primer._save_state(
            PrimingState(domain="soon.example.com", last_primed=500.0, clearance_expires=1400.0)
        )
        decision = primer.needs_priming("soon.example.com")
        assert decision.should_prime is True
        assert decision.outcome is PrimingOutcome.REFRESHED

    def test_unknown_expiry_uses_min_interval(self, tmp_path: Path) -> None:
        primer = BackgroundPrimer(_settings(tmp_path), pacer=_FakePacer(), clock=_Clock(10_000.0))
        # last primed long ago, unknown expiry -> refresh
        primer._save_state(
            PrimingState(domain="d.example.com", last_primed=1000.0, clearance_expires=0.0)
        )
        assert primer.needs_priming("d.example.com").should_prime is True
        # last primed recently, unknown expiry -> skip
        primer._save_state(
            PrimingState(domain="d.example.com", last_primed=9500.0, clearance_expires=0.0)
        )
        assert primer.needs_priming("d.example.com").should_prime is False


class TestSidecar:
    def test_round_trip(self, tmp_path: Path) -> None:
        primer = BackgroundPrimer(_settings(tmp_path), pacer=_FakePacer())
        state = PrimingState(domain="x.example.com", last_primed=1.0, clearance_expires=2.0)
        primer._save_state(state)
        loaded = primer.load_state("x.example.com")
        assert loaded == state

    def test_corrupt_file_is_tolerated(self, tmp_path: Path) -> None:
        primer = BackgroundPrimer(_settings(tmp_path), pacer=_FakePacer())
        path = primer._state_path("bad.example.com")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        loaded = primer.load_state("bad.example.com")
        assert loaded.last_primed == 0.0


class TestPrimeCycle:
    @pytest.mark.asyncio
    async def test_disabled_does_nothing(self, tmp_path: Path) -> None:
        pacer = _FakePacer()
        primer = BackgroundPrimer(
            _settings(tmp_path, bypass_background_priming_enabled=False), pacer=pacer
        )
        report = await primer.prime_domains(["a.example.com", "b.example.com"])
        assert report.disabled is True
        assert pacer.acquired == []
        assert report.primed == 0

    @pytest.mark.asyncio
    async def test_paces_and_primes_cold_domains(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pacer = _FakePacer()
        primer = BackgroundPrimer(_settings(tmp_path), pacer=pacer, clock=_Clock(1000.0))

        async def fake_visit(root: str, domain: str) -> list[dict[str, Any]]:
            return [{"name": "cf_clearance", "expires": 5000.0}]

        monkeypatch.setattr(primer, "_warm_and_read_cookies", fake_visit)
        report = await primer.prime_domains(["a.example.com", "a.example.com", "b.example.com"])
        # de-duplicated, one pace per unique domain
        assert pacer.acquired == ["a.example.com", "b.example.com"]
        assert report.primed == 2
        # state persisted with the captured expiry
        assert primer.load_state("a.example.com").clearance_expires == 5000.0

    @pytest.mark.asyncio
    async def test_budget_cap(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        pacer = _FakePacer()
        primer = BackgroundPrimer(
            _settings(tmp_path, bypass_priming_max_domains_per_cycle=1),
            pacer=pacer,
            clock=_Clock(1000.0),
        )

        async def fake_visit(root: str, domain: str) -> list[dict[str, Any]]:
            return [{"name": "cf_clearance", "expires": 5000.0}]

        monkeypatch.setattr(primer, "_warm_and_read_cookies", fake_visit)
        report = await primer.prime_domains(["a.example.com", "b.example.com"])
        assert report.primed == 1
        assert report.budget_exhausted == 1
        assert len(pacer.acquired) == 1

    @pytest.mark.asyncio
    async def test_no_clearance_is_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        primer = BackgroundPrimer(_settings(tmp_path), pacer=_FakePacer(), clock=_Clock(1000.0))

        async def fake_visit(root: str, domain: str) -> list[dict[str, Any]]:
            return [{"name": "session_id", "expires": 5000.0}]  # no clearance cookie

        monkeypatch.setattr(primer, "_warm_and_read_cookies", fake_visit)
        report = await primer.prime_domains(["a.example.com"])
        assert report.failed == 1
        assert report.primed == 0
        # no state written on failure
        assert primer.load_state("a.example.com").last_primed == 0.0

    @pytest.mark.asyncio
    async def test_visit_exception_does_not_abort_cycle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        primer = BackgroundPrimer(_settings(tmp_path), pacer=_FakePacer(), clock=_Clock(1000.0))

        async def boom(root: str, domain: str) -> list[dict[str, Any]]:
            if domain == "bad.example.com":
                raise RuntimeError("navigation failed")
            return [{"name": "cf_clearance", "expires": 5000.0}]

        monkeypatch.setattr(primer, "_warm_and_read_cookies", boom)
        report = await primer.prime_domains(["bad.example.com", "good.example.com"])
        assert report.failed == 1
        assert report.primed == 1
