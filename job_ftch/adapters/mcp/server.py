"""FastMCP server surface for multi-tenant job_ftch operations."""
# mypy: disable-error-code="untyped-decorator"

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from job_ftch.application.profile_inputs import build_candidate_profile_from_payload
from job_ftch.application.source_inputs import build_source_spec_from_input
from job_ftch.application.tenant_loader import load_tenants
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.config import Settings, get_settings
from job_ftch.domain import ManagedCandidateProfile


def _json_default(value: object) -> object:
    if hasattr(value, "isoformat"):
        return cast("Any", value).isoformat()
    if hasattr(value, "model_dump"):
        return cast("Any", value).model_dump(mode="json")
    return str(value)


class TenantMCPServer:
    def __init__(
        self,
        *,
        configs_dir: Path,
        base_settings: Settings | None = None,
        name: str = "job_ftch",
    ) -> None:
        try:
            from fastmcp import FastMCP
        except ImportError as exc:
            msg = "FastMCP server requires the 'mcp' extra: pip install job-ftch[mcp]"
            raise ImportError(msg) from exc

        self.configs_dir = configs_dir
        self.base_settings = base_settings or get_settings()
        self.name = name
        self.app = FastMCP(name)
        self.runner: TenantRunner | None = None
        self._register_surface()

    async def startup(self) -> None:
        if self.runner is not None:
            return
        tenants = load_tenants(self.configs_dir)
        self.runner = TenantRunner.from_tenants(tenants, base_settings=self.base_settings)

    async def shutdown(self) -> None:
        if self.runner is None:
            return
        await self.runner.close()
        self.runner = None

    def run(
        self,
        *,
        transport: Literal["stdio", "http", "sse", "streamable-http"] = "stdio",
        host: str = "127.0.0.1",
        port: int = 8000,
    ) -> None:
        self.app.run(transport=transport, host=host, port=port)

    def _register_surface(self) -> None:
        @self.app.tool
        async def run_pipeline(tenant_id: str) -> dict[str, Any]:
            summary = await self._require_runner().run_tenant(tenant_id)
            return summary.as_dict()

        @self.app.tool
        async def run_all_pipelines() -> list[dict[str, Any]]:
            summaries = await self._require_runner().run_all()
            return [summary.as_dict() for summary in summaries]

        @self.app.tool
        async def get_status(tenant_id: str) -> dict[str, Any] | None:
            summary = await self._require_runner().get_status(tenant_id)
            return None if summary is None else summary.as_dict()

        @self.app.tool
        async def list_source_health(tenant_id: str) -> list[dict[str, Any]]:
            return await self._require_runner().list_source_health(tenant_id)

        @self.app.tool
        async def list_sources(tenant_id: str) -> list[dict[str, Any]]:
            return await self._require_runner().list_sources(tenant_id)

        @self.app.tool
        async def add_source(
            tenant_id: str,
            link: str,
            source_type: str | None = None,
            limit: int = 100,
        ) -> dict[str, Any]:
            runner = self._require_runner()
            spec = await build_source_spec_from_input(
                link,
                auth_provider=runner.get_runtime(tenant_id).auth_provider,
                source_type=source_type,
                limit=limit,
            )
            return await runner.add_source_spec(
                tenant_id,
                spec,
                added_via="mcp",
                input_value=link,
            )

        @self.app.tool
        async def disable_source(tenant_id: str, source_id: str) -> dict[str, Any]:
            return await self._require_runner().disable_source(tenant_id, source_id)

        @self.app.tool
        async def list_profiles(tenant_id: str, user_id: str) -> list[dict[str, Any]]:
            return await self._require_runner().list_candidate_profiles(tenant_id, user_id)

        @self.app.tool
        async def save_profile(
            tenant_id: str,
            user_id: str,
            profile_id: str,
            summary: str,
        ) -> dict[str, Any]:
            profile = build_candidate_profile_from_payload(
                user_id=user_id,
                profile_id=profile_id,
                payload={"summary": summary, "name": profile_id},
            )
            saved = await self._require_runner().save_candidate_profile(
                tenant_id,
                ManagedCandidateProfile(
                    user_id=user_id,
                    profile_id=profile_id,
                    profile=profile,
                    updated_at=datetime.now(UTC),
                ),
            )
            await self._require_runner().set_active_candidate_profile(
                tenant_id, user_id, profile_id
            )
            return saved

        @self.app.tool
        async def activate_profile(
            tenant_id: str,
            user_id: str,
            profile_id: str,
        ) -> dict[str, Any]:
            return await self._require_runner().set_active_candidate_profile(
                tenant_id,
                user_id,
                profile_id,
            )

        @self.app.tool
        async def list_runs(tenant_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
            summaries = await self._require_runner().list_runs(tenant_id=tenant_id, limit=limit)
            return [summary.as_dict() for summary in summaries]

        @self.app.tool
        async def get_run(run_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
            summary = await self._require_runner().get_run(run_id, tenant_id=tenant_id)
            return None if summary is None else summary.as_dict()

        @self.app.tool
        async def list_tenants() -> list[dict[str, Any]]:
            tenants = await self._require_runner().list_tenants()
            return [tenant.model_dump(mode="json") for tenant in tenants]

        @self.app.tool
        async def list_browser_capabilities() -> dict[str, Any]:
            """Read-only inventory of browser/bypass routes and their availability."""
            from job_ftch.application.browser_capability_inventory import (
                inventory_to_public_dict,
            )

            inventory = self._require_runner().list_browser_capabilities()
            return inventory_to_public_dict(inventory)

        @self.app.tool
        async def explain_browser_route(
            tenant_id: str | None = None,
            source_id: str | None = None,
            bypass: str | None = None,
        ) -> dict[str, Any]:
            """Explain why a browser/bypass route is selected or unavailable."""
            from job_ftch.application.browser_capability_inventory import (
                explanation_to_public_dict,
            )

            explanation = await self._require_runner().explain_browser_route(
                tenant_id,
                source_id,
                bypass=bypass,
            )
            return explanation_to_public_dict(explanation)

        @self.app.tool
        async def ingest_resume(
            tenant_id: str,
            user_id: str,
            resume_text: str,
            profile_id: str | None = None,
            activate: bool = True,
        ) -> dict[str, Any]:
            """Ingest resume text into a managed candidate profile (not stored on sessions)."""
            record = await self._require_runner().ingest_resume(
                tenant_id,
                user_id=user_id,
                resume_text=resume_text,
                profile_id=profile_id,
                activate=activate,
            )
            resume = record.profile.resume
            summary = resume.summary if resume is not None else None
            if summary is None and record.profile.search_profiles:
                summary = record.profile.search_profiles[0].name
            return {
                "user_id": record.user_id,
                "profile_id": record.profile_id,
                "updated_at": record.updated_at.isoformat(),
                "summary": summary,
            }

        @self.app.tool
        async def create_search_session(
            tenant_id: str,
            user_id: str | None = None,
            profile_id: str | None = None,
            source_scope: list[str] | None = None,
            max_items: int | None = None,
            max_sources: int | None = None,
            result_limit: int = 20,
        ) -> dict[str, Any]:
            """Create a resume-driven search session for plan/approve/run workflow."""
            from job_ftch.application.search_session import session_to_public_dict

            session = await self._require_runner().create_search_session(
                tenant_id,
                user_id=user_id,
                profile_id=profile_id,
                source_scope=source_scope,
                max_items=max_items,
                max_sources=max_sources,
                result_limit=result_limit,
            )
            return session_to_public_dict(session)

        @self.app.tool
        async def plan_source_routes(session_id: str) -> dict[str, Any]:
            """Plan per-source routes for a search session using capability diagnostics."""
            from job_ftch.application.search_session import session_to_public_dict

            session = await self._require_runner().plan_source_routes(session_id)
            return session_to_public_dict(session)

        @self.app.tool
        async def approve_search_session(
            session_id: str,
            approved_source_ids: list[str] | None = None,
            approved_capability_ids: list[str] | None = None,
            approve_all_sensitive: bool = False,
            note: str | None = None,
        ) -> dict[str, Any]:
            """Approve sensitive routes/budgets before running a search session."""
            from job_ftch.application.search_session import session_to_public_dict

            session = await self._require_runner().approve_search_session(
                session_id,
                approved_source_ids=approved_source_ids,
                approved_capability_ids=approved_capability_ids,
                approve_all_sensitive=approve_all_sensitive,
                note=note,
            )
            return session_to_public_dict(session)

        @self.app.tool
        async def run_search_session(
            session_id: str,
            skip_pipeline: bool = False,
        ) -> dict[str, Any]:
            """Run a search session via existing tenant pipeline + profile ranking."""
            from job_ftch.application.search_session import session_to_public_dict

            session = await self._require_runner().run_search_session(
                session_id,
                skip_pipeline=skip_pipeline,
            )
            return session_to_public_dict(session)

        @self.app.tool
        async def get_search_session_status(session_id: str) -> dict[str, Any]:
            """Return search session status, route plan, and budgets."""
            from job_ftch.application.search_session import session_to_public_dict

            session = await self._require_runner().get_search_session_status(session_id)
            return session_to_public_dict(session)

        @self.app.tool
        async def list_search_results(
            session_id: str,
            limit: int | None = None,
        ) -> list[dict[str, Any]]:
            """List ranked job result refs for a search session."""
            refs = await self._require_runner().list_search_results(session_id, limit=limit)
            return [ref.model_dump(mode="json") for ref in refs]

        @self.app.tool
        async def explain_search_session(
            session_id: str,
            source_id: str | None = None,
            job_id: str | None = None,
        ) -> dict[str, Any]:
            """Explain rejected/degraded sources or non-selected jobs."""
            from job_ftch.application.search_session import explanation_to_dict

            explanation = await self._require_runner().explain_search_session(
                session_id,
                source_id=source_id,
                job_id=job_id,
            )
            return explanation_to_dict(explanation)

        @self.app.tool
        async def cancel_search_session(session_id: str) -> dict[str, Any]:
            """Cancel a search session (cooperative if a run is in flight)."""
            from job_ftch.application.search_session import session_to_public_dict

            session = await self._require_runner().cancel_search_session(session_id)
            return session_to_public_dict(session)

        @self.app.tool
        async def search_jobs(
            query: str,
            tenant_id: str | None = None,
            user_id: str | None = None,
            limit: int = 20,
        ) -> list[dict[str, Any]]:
            limit = min(limit, 100)
            groups = await self._require_runner().search_jobs(
                query, tenant_id=tenant_id, user_id=user_id, limit=limit
            )
            return [group.model_dump(mode="json") for group in groups]

        @self.app.tool
        async def get_job(job_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
            job = await self._require_runner().get_job(job_id, tenant_id=tenant_id)
            return None if job is None else job.model_dump(mode="json")

        @self.app.tool
        async def get_job_lineage(
            job_id: str,
            tenant_id: str | None = None,
        ) -> dict[str, Any] | None:
            lineage = await self._require_runner().get_job_lineage(job_id, tenant_id=tenant_id)
            return None if lineage is None else lineage.model_dump(mode="json")

        @self.app.tool
        async def reset_tenant(tenant_id: str) -> None:
            await self._require_runner().reset_tenant(tenant_id)

        @self.app.resource("jobs://{tenant_id}/latest")
        async def latest_jobs_resource(tenant_id: str) -> str:
            jobs = await self._require_runner().latest_jobs(tenant_id, limit=10)
            return json.dumps(
                [job.model_dump(mode="json") for job in jobs],
                ensure_ascii=False,
                default=_json_default,
            )

        @self.app.resource("jobs://{tenant_id}/run_summary")
        async def run_summary_resource(tenant_id: str) -> str:
            summary = await self._require_runner().get_status(tenant_id)
            return json.dumps(
                None if summary is None else summary.as_dict(),
                ensure_ascii=False,
                default=_json_default,
            )

        @self.app.resource("config://{tenant_id}")
        async def config_resource(tenant_id: str) -> str:
            config = await self._require_runner().get_config(tenant_id)
            return json.dumps(config, ensure_ascii=False, default=_json_default)

    def _require_runner(self) -> TenantRunner:
        if self.runner is None:
            msg = "TenantMCPServer.startup() must run before using MCP tools."
            raise RuntimeError(msg)
        return self.runner


def create_server(
    *,
    configs_dir: str | Path,
    base_settings: Settings | None = None,
    name: str = "job_ftch",
) -> TenantMCPServer:
    return TenantMCPServer(
        configs_dir=Path(configs_dir),
        base_settings=base_settings,
        name=name,
    )
