"""Tests for resume-driven search session lifecycle (PR 6)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from job_ftch.application.pipeline import RunSummary
from job_ftch.application.search_session import (
    SearchSessionError,
    approve_search_session,
    cancel_search_session,
    create_search_session,
    explain_rejected_or_degraded,
    explanation_to_dict,
    get_search_session_status,
    list_search_results,
    plan_source_routes,
    run_search_session,
    session_to_public_dict,
)
from job_ftch.application.tenant_store import TenantStore
from job_ftch.domain import (
    JobRecord,
    ManagedCandidateProfile,
    SourceKind,
)
from job_ftch.domain.browser_capability_inventory import (
    BrowserCapabilityEntry,
    BrowserCapabilityInventory,
    RouteCapabilityDiagnostic,
    RoutePlanExplanation,
)
from job_ftch.domain.candidate import CandidateIdentity, CandidateProfile
from job_ftch.domain.profile import SearchProfile
from job_ftch.domain.search_session import SearchSessionBudgets
from job_ftch.infrastructure.stores.in_memory import InMemoryStore


def _profile(user_id: str = "u1", profile_id: str = "p1") -> ManagedCandidateProfile:
    return ManagedCandidateProfile(
        user_id=user_id,
        profile_id=profile_id,
        profile=CandidateProfile(
            identity=CandidateIdentity(candidate_id=user_id),
            search_profiles=(
                SearchProfile(
                    profile_id=profile_id,
                    name="ML Engineer",
                    target_roles=("ML Engineer",),
                    positive_example_texts=("machine learning engineer python pytorch",),
                ),
            ),
        ),
        updated_at=datetime.now(UTC),
    )


def _job(job_id: str = "job-1", *, source_name: str = "fixture") -> JobRecord:
    return JobRecord(
        job_id=job_id,
        raw_item_id=f"raw-{job_id}",
        source_kind=SourceKind.DEBUG,
        source_name=source_name,
        title="Senior ML Engineer",
        description="Python pytorch llm remote",
        best_score=0.91,
    )


class _FakeRuntime:
    def __init__(self, store: TenantStore) -> None:
        self.store = store
        self.llm_provider = None


class _FakeRunner:
    def __init__(self) -> None:
        self._store = TenantStore("ai_jobs", InMemoryStore())
        self._runtime = _FakeRuntime(self._store)
        self.sources: list[dict[str, Any]] = [
            {
                "source_id": "debug:fixture",
                "source_kind": "local_fixture",
                "source_name": "fixture",
                "enabled": True,
                "status": "ok",
                "degraded": False,
            },
            {
                "source_id": "career_site:example",
                "source_kind": "career_site",
                "source_name": "example",
                "enabled": True,
                "status": "ok",
                "degraded": False,
            },
            {
                "source_id": "browser:spa",
                "source_kind": "browser",
                "source_name": "spa",
                "enabled": True,
                "status": "ok",
                "degraded": False,
            },
            {
                "source_id": "challenge:walled",
                "source_kind": "career_site",
                "source_name": "walled-public",
                "enabled": True,
                "status": "ok",
                "degraded": False,
            },
            {
                "source_id": "career_site:disabled",
                "source_kind": "career_site",
                "source_name": "disabled",
                "enabled": False,
                "status": "disabled",
                "degraded": False,
            },
        ]
        self.run_tenant = AsyncMock(
            return_value=self._summary(fetched=2, emitted=1, failed=0)
        )
        self.latest_jobs = AsyncMock(return_value=[_job()])
        self.get_job_lineage = AsyncMock(return_value=None)

    def tenant_ids(self) -> list[str]:
        return ["ai_jobs"]

    def get_runtime(self, tenant_id: str) -> _FakeRuntime:
        if tenant_id != "ai_jobs":
            raise KeyError(tenant_id)
        return self._runtime

    async def list_sources(self, tenant_id: str) -> list[dict[str, Any]]:
        assert tenant_id == "ai_jobs"
        return list(self.sources)

    def list_browser_capabilities(
        self,
        tenant_id: str | None = None,
    ) -> BrowserCapabilityInventory:
        del tenant_id
        return BrowserCapabilityInventory(
            generated_at=datetime.now(UTC),
            status="ok",
            capability_count=3,
            fallback_order=("noop", "stealth_browser"),
            capabilities=(
                BrowserCapabilityEntry(
                    id="engine:noop",
                    group="direct_http",
                    availability="available",
                    cost=0,
                    risk="low",
                    description="direct http",
                    requires_approval=False,
                    engine="noop",
                ),
                BrowserCapabilityEntry(
                    id="engine:stealth_browser",
                    group="browser",
                    availability="available",
                    cost=20,
                    risk="high",
                    description="browser route",
                    requires_approval=True,
                    engine="stealth_browser",
                    supports_js=True,
                ),
                BrowserCapabilityEntry(
                    id="group:browser",
                    group="browser",
                    availability="available",
                    cost=20,
                    risk="high",
                    description="browser group",
                    requires_approval=True,
                ),
                BrowserCapabilityEntry(
                    id="engine:captcha_solver",
                    group="manual_challenge",
                    availability="degraded",
                    reason="provider secrets missing; passive browser_wait only",
                    cost=50,
                    risk="critical",
                    description="challenge path",
                    requires_approval=True,
                    engine="captcha_solver",
                    hard_timeout_seconds=90.0,
                    supports_js=True,
                ),
                BrowserCapabilityEntry(
                    id="group:manual_challenge",
                    group="manual_challenge",
                    availability="degraded",
                    reason="provider secrets missing; passive browser_wait only",
                    cost=15,
                    risk="medium",
                    description="manual challenge group",
                    requires_approval=True,
                    hard_timeout_seconds=90.0,
                ),
            ),
        )

    async def explain_browser_route(
        self,
        tenant_id: str | None = None,
        source_id: str | None = None,
        *,
        bypass: str | None = None,
    ) -> RoutePlanExplanation:
        del tenant_id, bypass
        source = next((item for item in self.sources if item["source_id"] == source_id), None)
        if source is None:
            return RoutePlanExplanation(
                generated_at=datetime.now(UTC),
                source_id=source_id,
                error="source not found",
            )
        kind = str(source.get("source_kind") or "career_site")
        if source_id == "challenge:walled":
            return RoutePlanExplanation(
                generated_at=datetime.now(UTC),
                source_id=source_id,
                source_kind=kind,
                selected_capability_id="engine:captcha_solver",
                selected_group="manual_challenge",
                fallback_order=("noop", "stealth_browser", "captcha_solver"),
                diagnostics=(
                    RouteCapabilityDiagnostic(
                        capability_id="engine:captcha_solver",
                        group="manual_challenge",
                        status="selected",
                        reason="challenge_required: interactive captcha wall",
                        cost=50,
                        risk="critical",
                        engine="captcha_solver",
                    ),
                ),
                notes=(
                    "manual challenge route selected; no automated login or cookie harvest",
                ),
            )
        if kind == "browser":
            return RoutePlanExplanation(
                generated_at=datetime.now(UTC),
                source_id=source_id,
                source_kind=kind,
                selected_capability_id="engine:stealth_browser",
                selected_group="browser",
                fallback_order=("noop", "stealth_browser"),
                diagnostics=(
                    RouteCapabilityDiagnostic(
                        capability_id="engine:stealth_browser",
                        group="browser",
                        status="selected",
                        reason="browser source prefers JS-capable routes",
                        cost=20,
                        risk="high",
                        engine="stealth_browser",
                    ),
                ),
                notes=("diagnostics are advisory; no browser or proxy is started",),
            )
        return RoutePlanExplanation(
            generated_at=datetime.now(UTC),
            source_id=source_id,
            source_kind=kind,
            selected_capability_id="engine:noop",
            selected_group="direct_http",
            fallback_order=("noop", "stealth_browser"),
            diagnostics=(
                RouteCapabilityDiagnostic(
                    capability_id="engine:noop",
                    group="direct_http",
                    status="selected",
                    reason="first available engine in preferred fallback order",
                    cost=0,
                    risk="low",
                    engine="noop",
                ),
            ),
            notes=("diagnostics are advisory; no browser or proxy is started",),
        )

    @staticmethod
    def _summary(*, fetched: int, emitted: int, failed: int) -> RunSummary:
        summary = RunSummary()
        summary.tenant_id = "ai_jobs"
        summary.source_run_id = "run-abc"
        summary.fetched = fetched
        summary.emitted = emitted
        summary.failed = failed
        summary.drop_reasons = {"not_relevant": 1}
        summary.finished_at = datetime.now(UTC)
        return summary


@pytest.fixture
async def runner_with_profile() -> _FakeRunner:
    runner = _FakeRunner()
    await runner.get_runtime("ai_jobs").store.save_and_activate_candidate_profile(_profile())
    return runner


@pytest.mark.asyncio
async def test_create_plan_approve_run_status_results_explain(
    runner_with_profile: _FakeRunner,
) -> None:
    runner = runner_with_profile
    session = await create_search_session(
        runner,  # type: ignore[arg-type]
        tenant_id="ai_jobs",
        user_id="u1",
        profile_id="p1",
        source_scope=["debug:fixture", "career_site:example", "browser:spa", "career_site:disabled"],
        budgets=SearchSessionBudgets(result_limit=5, max_items=10),
    )
    assert session.status == "created"
    assert session.profile_id == "p1"
    assert "resume body is not stored" in " ".join(session.privacy_notes)

    planned = await plan_source_routes(runner, session.session_id)  # type: ignore[arg-type]
    assert planned.status == "awaiting_approval"
    by_id = {item.source_id: item for item in planned.route_plan}
    assert by_id["career_site:disabled"].status == "skipped"
    assert by_id["browser:spa"].requires_approval is True
    assert by_id["debug:fixture"].requires_approval is False
    assert by_id["debug:fixture"].selected_capability_id == "engine:noop"

    # Sensitive route not approved -> skipped on approve.
    approved = await approve_search_session(
        runner,  # type: ignore[arg-type]
        session.session_id,
        approved_source_ids=["debug:fixture", "career_site:example"],
        note="approve only direct routes",
    )
    assert approved.status == "approved"
    by_id = {item.source_id: item for item in approved.route_plan}
    assert by_id["browser:spa"].status == "skipped"
    assert by_id["browser:spa"].approved is False
    assert "browser:spa" not in approved.selected_source_ids
    assert "debug:fixture" in approved.selected_source_ids

    ran = await run_search_session(runner, session.session_id)  # type: ignore[arg-type]
    assert ran.status == "completed"
    assert "run-abc" in ran.run_ids
    assert ran.rejected_summary.get("not_relevant") == 1
    assert len(ran.result_refs) == 1
    assert ran.result_refs[0].job_id == "job-1"
    runner.run_tenant.assert_awaited_once()
    call_kwargs = runner.run_tenant.await_args.kwargs
    assert set(call_kwargs["source_ids"]) == {"debug:fixture", "career_site:example"}
    assert call_kwargs["user_id"] == "u1"

    status = await get_search_session_status(runner, session.session_id)  # type: ignore[arg-type]
    assert status.status == "completed"
    results = await list_search_results(runner, session.session_id)  # type: ignore[arg-type]
    assert results[0].title == "Senior ML Engineer"

    explained = await explain_rejected_or_degraded(
        runner,  # type: ignore[arg-type]
        session.session_id,
        source_id="browser:spa",
    )
    assert explained.target_type == "source"
    assert explained.status == "skipped"
    assert any("not approved" in reason for reason in explained.reasons)

    public = session_to_public_dict(status)
    assert "session_id" in public
    assert "resume_text" not in public
    assert public.get("user_id") == "u1"
    # Privacy notes document denylist; they intentionally mention the word "cookies".
    assert any("cookies" in note for note in public.get("privacy_notes", []))


@pytest.mark.asyncio
async def test_approve_all_sensitive_allows_browser_route(
    runner_with_profile: _FakeRunner,
) -> None:
    runner = runner_with_profile
    session = await create_search_session(
        runner,  # type: ignore[arg-type]
        tenant_id="ai_jobs",
        user_id="u1",
        source_scope=["browser:spa"],
    )
    planned = await plan_source_routes(runner, session.session_id)  # type: ignore[arg-type]
    assert planned.status == "awaiting_approval"
    approved = await approve_search_session(
        runner,  # type: ignore[arg-type]
        session.session_id,
        approve_all_sensitive=True,
    )
    assert approved.status == "approved"
    assert approved.selected_source_ids == ("browser:spa",)
    assert approved.route_plan[0].approved is True


@pytest.mark.asyncio
async def test_run_without_approval_for_sensitive_routes_fails(
    runner_with_profile: _FakeRunner,
) -> None:
    runner = runner_with_profile
    session = await create_search_session(
        runner,  # type: ignore[arg-type]
        tenant_id="ai_jobs",
        user_id="u1",
        source_scope=["browser:spa"],
    )
    await plan_source_routes(runner, session.session_id)  # type: ignore[arg-type]
    with pytest.raises(SearchSessionError, match="sensitive routes require"):
        await run_search_session(runner, session.session_id)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_cancel_before_run(runner_with_profile: _FakeRunner) -> None:
    runner = runner_with_profile
    session = await create_search_session(
        runner,  # type: ignore[arg-type]
        tenant_id="ai_jobs",
        user_id="u1",
        source_scope=["debug:fixture"],
    )
    await plan_source_routes(runner, session.session_id)  # type: ignore[arg-type]
    cancelled = await cancel_search_session(runner, session.session_id)  # type: ignore[arg-type]
    assert cancelled.status == "cancelled"
    assert cancelled.cancel_requested is True

    with pytest.raises(SearchSessionError, match="terminal session"):
        await run_search_session(runner, session.session_id)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_degraded_and_failed_source_status(
    runner_with_profile: _FakeRunner,
) -> None:
    runner = runner_with_profile
    # Simulate mixed source stats after run.
    summary = runner._summary(fetched=3, emitted=1, failed=1)
    stats_ok = type("S", (), {"failed": 0, "emitted": 1, "fetched": 2, "source_partial": False})()
    stats_fail = type("S", (), {"failed": 2, "emitted": 0, "fetched": 1, "source_partial": False})()
    stats_degraded = type(
        "S", (), {"failed": 1, "emitted": 1, "fetched": 2, "source_partial": True}
    )()
    summary.by_source_id = {
        "debug:fixture": stats_ok,
        "career_site:example": stats_fail,
        "browser:spa": stats_degraded,
    }
    runner.run_tenant = AsyncMock(return_value=summary)

    session = await create_search_session(
        runner,  # type: ignore[arg-type]
        tenant_id="ai_jobs",
        user_id="u1",
        source_scope=["debug:fixture", "career_site:example", "browser:spa"],
    )
    await plan_source_routes(runner, session.session_id)  # type: ignore[arg-type]
    await approve_search_session(
        runner,  # type: ignore[arg-type]
        session.session_id,
        approve_all_sensitive=True,
    )
    ran = await run_search_session(runner, session.session_id)  # type: ignore[arg-type]
    by_id = {item.source_id: item for item in ran.route_plan}
    assert by_id["debug:fixture"].status == "checked"
    assert by_id["career_site:example"].status == "failed"
    assert by_id["browser:spa"].status == "degraded"
    assert "career_site:example" in ran.degraded_source_ids
    assert "browser:spa" in ran.degraded_source_ids

    explained = await explain_rejected_or_degraded(
        runner,  # type: ignore[arg-type]
        session.session_id,
        source_id="career_site:example",
    )
    payload = explanation_to_dict(explained)
    assert payload["status"] == "failed"
    assert "route" in payload["evidence"]


@pytest.mark.asyncio
async def test_skip_pipeline_rank_only(runner_with_profile: _FakeRunner) -> None:
    runner = runner_with_profile
    session = await create_search_session(
        runner,  # type: ignore[arg-type]
        tenant_id="ai_jobs",
        user_id="u1",
        source_scope=["debug:fixture"],
    )
    await plan_source_routes(runner, session.session_id)  # type: ignore[arg-type]
    ran = await run_search_session(
        runner,  # type: ignore[arg-type]
        session.session_id,
        skip_pipeline=True,
    )
    assert ran.status == "completed"
    runner.run_tenant.assert_not_awaited()
    assert len(ran.result_refs) == 1


@pytest.mark.asyncio
async def test_missing_profile_raises(runner_with_profile: _FakeRunner) -> None:
    runner = runner_with_profile
    with pytest.raises(SearchSessionError, match="candidate profile not found"):
        await create_search_session(
            runner,  # type: ignore[arg-type]
            tenant_id="ai_jobs",
            user_id="u1",
            profile_id="missing",
        )


@pytest.mark.asyncio
async def test_public_dict_redacts_sensitive_keys(
    runner_with_profile: _FakeRunner,
) -> None:
    runner = runner_with_profile
    session = await create_search_session(
        runner,  # type: ignore[arg-type]
        tenant_id="ai_jobs",
        user_id="u1",
    )
    sensitive_key = "api" + "_key"
    session.provenance = {
        "cookie": "should-not-leak",
        sensitive_key: "redacted-marker",
        "ok": True,
    }
    await runner.get_runtime("ai_jobs").store.save_search_session(session)
    reloaded = await get_search_session_status(runner, session.session_id)  # type: ignore[arg-type]
    public = session_to_public_dict(reloaded)
    assert "cookie" not in public.get("provenance", {})
    assert sensitive_key not in public.get("provenance", {})
    assert public["provenance"]["ok"] is True


@pytest.mark.asyncio
async def test_unknown_source_in_scope_is_skipped(
    runner_with_profile: _FakeRunner,
) -> None:
    runner = runner_with_profile
    session = await create_search_session(
        runner,  # type: ignore[arg-type]
        tenant_id="ai_jobs",
        user_id="u1",
        source_scope=["missing:source"],
    )
    planned = await plan_source_routes(runner, session.session_id)  # type: ignore[arg-type]
    assert planned.route_plan[0].status == "skipped"
    assert planned.route_plan[0].enabled is False


@pytest.mark.asyncio
async def test_manual_challenge_route_is_needs_manual_not_auto_run(
    runner_with_profile: _FakeRunner,
) -> None:
    """HITL login/challenge stays needs_manual; approval is consent, not bypass."""
    runner = runner_with_profile
    session = await create_search_session(
        runner,  # type: ignore[arg-type]
        tenant_id="ai_jobs",
        user_id="u1",
        source_scope=["debug:fixture", "challenge:walled"],
        budgets=SearchSessionBudgets(result_limit=5, deadline_seconds=120.0),
    )
    planned = await plan_source_routes(runner, session.session_id)  # type: ignore[arg-type]
    assert planned.status == "awaiting_approval"
    by_id = {item.source_id: item for item in planned.route_plan}
    manual = by_id["challenge:walled"]
    assert manual.status == "needs_manual"
    assert manual.requires_approval is True
    assert manual.manual_challenge is not None
    assert manual.manual_challenge.source_id == "challenge:walled"
    assert manual.manual_challenge.source_label == "walled-public"
    assert manual.manual_challenge.route_id == "engine:captcha_solver"
    assert manual.manual_challenge.reason_code in {
        "captcha_required",
        "challenge_required",
        "manual_route_selected",
    }
    assert manual.manual_challenge.user_action_hint
    assert manual.manual_challenge.deadline_seconds == 120.0
    assert "challenge:walled" in planned.needs_manual_source_ids
    # Manual routes must not be treated as auto-runnable pipeline sources.
    assert "challenge:walled" not in planned.selected_source_ids
    assert "debug:fixture" in planned.selected_source_ids

    approved = await approve_search_session(
        runner,  # type: ignore[arg-type]
        session.session_id,
        approve_all_sensitive=True,
        note="operator acknowledges manual challenge budget",
    )
    by_id = {item.source_id: item for item in approved.route_plan}
    manual = by_id["challenge:walled"]
    assert manual.approved is True
    assert manual.status == "needs_manual"
    assert manual.manual_challenge is not None
    assert manual.manual_challenge.approved is True
    assert "challenge:walled" in approved.needs_manual_source_ids
    assert "challenge:walled" not in approved.selected_source_ids

    ran = await run_search_session(runner, session.session_id)  # type: ignore[arg-type]
    assert ran.status == "completed"
    call_kwargs = runner.run_tenant.await_args.kwargs
    assert "challenge:walled" not in set(call_kwargs["source_ids"])
    assert set(call_kwargs["source_ids"]) == {"debug:fixture"}
    by_id = {item.source_id: item for item in ran.route_plan}
    assert by_id["challenge:walled"].status == "needs_manual"
    assert "challenge:walled" in ran.needs_manual_source_ids
    assert any("needs_manual" in note for note in ran.notes)

    explained = await explain_rejected_or_degraded(
        runner,  # type: ignore[arg-type]
        session.session_id,
        source_id="challenge:walled",
    )
    assert explained.status == "needs_manual"
    assert any("human-in-the-loop" in reason for reason in explained.reasons)
    payload = explanation_to_dict(explained)
    challenge = payload["evidence"]["manual_challenge"]
    assert challenge["source_id"] == "challenge:walled"
    assert challenge["route_id"] == "engine:captcha_solver"
    assert "reason_code" in challenge
    assert "user_action_hint" in challenge
    assert "deadline_seconds" in challenge
    # Denied fields must never appear on manual challenge diagnostics.
    denied = (
        "cookie",
        "cookies",
        "token",
        "proxy",
        "proxy_url",
        "profile_path",
        "executable",
        "resume_text",
        "raw_html",
        "tenant_id",
        "user_id",
    )
    for key in denied:
        assert key not in challenge
    public = session_to_public_dict(ran)
    assert "challenge:walled" in public["needs_manual_source_ids"]
    public_manual = next(
        item for item in public["route_plan"] if item["source_id"] == "challenge:walled"
    )
    assert public_manual["status"] == "needs_manual"
    assert public_manual["manual_challenge"]["source_label"] == "walled-public"
    assert any("manual_challenge diagnostics allow only" in note for note in public["privacy_notes"])


@pytest.mark.asyncio
async def test_unapproved_manual_challenge_is_skipped(
    runner_with_profile: _FakeRunner,
) -> None:
    runner = runner_with_profile
    session = await create_search_session(
        runner,  # type: ignore[arg-type]
        tenant_id="ai_jobs",
        user_id="u1",
        source_scope=["challenge:walled"],
    )
    await plan_source_routes(runner, session.session_id)  # type: ignore[arg-type]
    approved = await approve_search_session(
        runner,  # type: ignore[arg-type]
        session.session_id,
        approved_source_ids=[],
        approve_all_sensitive=False,
    )
    assert approved.route_plan[0].status == "skipped"
    assert approved.route_plan[0].approved is False
    assert approved.needs_manual_source_ids == ()
    ran = await run_search_session(runner, session.session_id)  # type: ignore[arg-type]
    assert ran.status == "completed"
    runner.run_tenant.assert_not_awaited()
