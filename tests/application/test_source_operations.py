from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from job_ftch.application.source_operations import (
    probe_bypass_route,
    probe_source,
    run_browser_probe,
    run_source,
    run_source_escalation,
)
from job_ftch.domain.browser_capability_inventory import (
    RouteCapabilityDiagnostic,
    RoutePlanExplanation,
)


class _FakeRunner:
    def __init__(self) -> None:
        self.sources = [
            {
                "source_id": "debug:fixture",
                "source_kind": "local_fixture",
                "source_name": "fixture",
                "enabled": True,
                "status": "pending",
                "degraded": False,
                "requirements": {"browser_required": False},
                "spec": {"bypass": None, "parser": "generic"},
            },
            {
                "source_id": "career_site:off",
                "source_kind": "career_site",
                "source_name": "off",
                "enabled": False,
                "status": "disabled",
                "degraded": False,
                "requirements": {"browser_required": False},
                "spec": {"bypass": "noop"},
            },
        ]
        self.run_tenant = AsyncMock(
            return_value=SimpleNamespace(
                as_dict=lambda: {
                    "tenant_id": "t1",
                    "source_run_id": "run-1",
                    "fetched": 2,
                    "emitted": 1,
                    "failed": 0,
                    "skipped_already_active": False,
                    "source_failures": [],
                    "source_outcomes": [],
                    "drop_reasons": {},
                }
            )
        )

    async def list_sources(self, tenant_id: str) -> list[dict[str, Any]]:
        del tenant_id
        return list(self.sources)

    async def explain_browser_route(
        self,
        tenant_id: str | None = None,
        source_id: str | None = None,
        *,
        bypass: str | None = None,
    ) -> RoutePlanExplanation:
        del tenant_id
        requested = bypass
        engine = requested or "noop"
        group = "browser" if engine in {"cloak", "nodriver", "camoufox"} else "direct_http"
        available = engine != "missing_engine"
        return RoutePlanExplanation(
            generated_at=datetime.now(UTC),
            source_id=source_id,
            source_kind="local_fixture",
            requested_bypass=requested,
            selected_capability_id=f"engine:{engine}" if available else None,
            selected_group=group if available else None,
            fallback_order=("noop", "curl_stealth", "cloak"),
            diagnostics=(
                RouteCapabilityDiagnostic(
                    capability_id=f"engine:{engine}",
                    group=group,  # type: ignore[arg-type]
                    status="selected" if available else "unavailable",
                    reason="test route",
                    cost=0,
                    risk="low",
                    engine=engine,
                ),
            ),
            notes=("test",),
        )


@pytest.mark.asyncio
async def test_probe_source_cheap_does_not_run() -> None:
    runner = _FakeRunner()
    payload = await probe_source(
        runner, tenant_id="t1", source_id="debug:fixture", mode="cheap", max_items=5
    )
    assert payload["ok"] is True
    assert payload["executed"] is False
    assert payload["selected_route"]["engine"] == "noop"
    runner.run_tenant.assert_not_called()


@pytest.mark.asyncio
async def test_probe_source_full_runs_tenant() -> None:
    runner = _FakeRunner()
    payload = await probe_source(
        runner, tenant_id="t1", source_id="debug:fixture", mode="full", max_items=3
    )
    assert payload["executed"] is True
    assert payload["run"]["fetched"] == 2
    runner.run_tenant.assert_awaited_once()
    kwargs = runner.run_tenant.await_args.kwargs
    assert kwargs["source_ids"] == ["debug:fixture"]
    assert kwargs["max_items"] == 3


@pytest.mark.asyncio
async def test_run_source_pins_foreign_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "job_ftch.application.source_operations._registered_bypass_names",
        lambda: {"cloak", "noop", "curl_stealth"},
    )
    runner = _FakeRunner()
    payload = await run_source(
        runner,
        tenant_id="t1",
        source_id="debug:fixture",
        bypass="cloak",
    )
    assert payload["executed"] is True
    assert payload["parse"]["stage"] in {"ingest", "fetch", "parse", "pipeline"}
    runner.run_tenant.assert_awaited_once()
    kwargs = runner.run_tenant.await_args.kwargs
    assert kwargs["bypass_override"] == "cloak"
    assert kwargs["ignore_schedule_gates"] is True


@pytest.mark.asyncio
async def test_run_source_escalation_all_sweeps_ladder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "job_ftch.application.source_operations._registered_bypass_names",
        lambda: {"cloak", "noop", "curl_stealth"},
    )
    runner = _FakeRunner()
    payload = await run_source_escalation(
        runner,
        tenant_id="t1",
        source_id="debug:fixture",
        strategy="all",
    )
    assert payload["status"] in {"ok", "degraded"}
    assert payload["escalation_ladder"] == ["noop", "curl_stealth", "cloak"]
    assert isinstance(payload["attempts"], list)
    assert len(payload["attempts"]) == len(payload["escalation_ladder"])
    assert all("parse" in item and "stage" in item["parse"] for item in payload["attempts"])
    assert runner.run_tenant.await_count >= 1


@pytest.mark.asyncio
async def test_run_source_escalation_all_stops_at_max_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "job_ftch.application.source_operations._registered_bypass_names",
        lambda: {"cloak", "noop", "curl_stealth"},
    )
    runner = _FakeRunner()
    payload = await run_source_escalation(
        runner,
        tenant_id="t1",
        source_id="debug:fixture",
        strategy="all",
        max_tier="noop",
    )
    assert payload["escalation_ladder"] == ["noop"]
    assert len(payload["attempts"]) == 1
    assert payload["attempts"][0]["bypass"] == "noop"
    assert payload["attempts"][0]["parse"]["stage"]
    assert runner.run_tenant.await_count == 1


@pytest.mark.asyncio
async def test_probe_bypass_route_executes_selected_http_engine() -> None:
    runner = _FakeRunner()
    payload = await probe_bypass_route(
        runner,
        tenant_id="t1",
        source_id="debug:fixture",
        bypass="noop",
        max_items=2,
    )
    assert payload["executed"] is True
    runner.run_tenant.assert_awaited_once()


@pytest.mark.asyncio
async def test_probe_bypass_route_pins_browser_engine_without_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "job_ftch.application.source_operations._registered_bypass_names",
        lambda: {"cloak", "noop", "curl_stealth"},
    )
    runner = _FakeRunner()
    payload = await probe_bypass_route(
        runner,
        tenant_id="t1",
        source_id="debug:fixture",
        bypass="cloak",
        max_items=2,
    )
    assert payload["executed"] is True
    assert payload["parse"] is not None
    runner.run_tenant.assert_awaited_once()
    assert runner.run_tenant.await_args.kwargs["bypass_override"] == "cloak"


@pytest.mark.asyncio
async def test_run_browser_probe_requires_listing_url() -> None:
    runner = _FakeRunner()
    payload = await run_browser_probe(
        runner,
        tenant_id="t1",
        source_id="debug:fixture",
        probe="listing",
        engine="auto",
    )
    assert payload["status"] == "unsupported"
    assert payload["executed"] is False
    assert payload["error"] == "listing_url_required"
    assert payload["route"]["selected_capability_id"] == "engine:noop"
    runner.run_tenant.assert_not_called()


@pytest.mark.asyncio
async def test_run_browser_probe_listing_uses_runner_port() -> None:
    runner = _FakeRunner()
    runner.probe_browser_listing = AsyncMock(
        return_value={
            "ok": True,
            "status": "ok",
            "executed": True,
            "engine": "patchright_browser",
            "final_url": "https://example.com/jobs",
            "page_title": "Jobs",
            "item_count": 1,
            "items": [{"url": "https://example.com/jobs/1", "title": "Engineer"}],
            "notes": ["listing probe opens one ephemeral headless page"],
        }
    )
    payload = await run_browser_probe(
        runner,
        tenant_id="t1",
        source_id="debug:fixture",
        url="https://example.com/jobs",
        probe="listing",
        engine="auto",
        max_items=3,
    )
    assert payload["ok"] is True
    assert payload["executed"] is True
    assert payload["status"] == "ok"
    assert payload["engine"] == "patchright_browser"
    assert payload["items"][0]["url"] == "https://example.com/jobs/1"
    runner.probe_browser_listing.assert_awaited_once()
    kwargs = runner.probe_browser_listing.await_args.kwargs
    assert kwargs["url"] == "https://example.com/jobs"
    assert kwargs["engine"] == "patchright_browser"
    assert kwargs["max_items"] == 3
    runner.run_tenant.assert_not_called()


@pytest.mark.asyncio
async def test_run_browser_probe_detail_is_not_implemented() -> None:
    runner = _FakeRunner()
    runner.probe_browser_listing = AsyncMock()
    payload = await run_browser_probe(
        runner,
        tenant_id="t1",
        source_id="debug:fixture",
        url="https://example.com/jobs",
        probe="detail",
        engine="auto",
    )
    assert payload["status"] == "not_implemented"
    assert payload["executed"] is False
    assert payload["missing_service"] == "browser_session_probe"
    runner.probe_browser_listing.assert_not_called()
    runner.run_tenant.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_source_is_not_run() -> None:
    runner = _FakeRunner()
    payload = await run_source(runner, tenant_id="t1", source_id="career_site:off")
    assert payload["status"] == "source_disabled"
    runner.run_tenant.assert_not_called()
