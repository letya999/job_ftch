"""FastMCP server surface for multi-tenant job_ftch operations."""
# mypy: disable-error-code="untyped-decorator"

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urljoin

from job_ftch.adapters.mcp.product_surface import (
    add_shots,
    filter_job_groups,
    resolve_surface,
)
from job_ftch.adapters.mcp.product_surface import (
    list_shots as list_shots_action,
)
from job_ftch.adapters.mcp.product_surface import (
    remove_shot as remove_shot_action,
)
from job_ftch.adapters.mcp.product_surface import (
    upsert_source as upsert_source_action,
)
from job_ftch.application.profile_inputs import build_candidate_profile_from_payload
from job_ftch.application.tenant_loader import load_tenants
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.config import Settings, get_settings
from job_ftch.domain import ManagedCandidateProfile

TransportName = Literal["stdio", "http", "sse", "streamable-http"]


def _json_default(value: object) -> object:
    if hasattr(value, "isoformat"):
        return cast("Any", value).isoformat()
    if hasattr(value, "model_dump"):
        return cast("Any", value).model_dump(mode="json")
    return str(value)


def _tool_annotations(
    *,
    read_only: bool = False,
    destructive: bool = False,
    idempotent: bool = False,
    open_world: bool = False,
) -> Any:
    """Build MCP tool annotations; degrade gracefully if types unavailable."""
    try:
        from mcp.types import ToolAnnotations
    except ImportError:
        return None
    return ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=idempotent,
        openWorldHint=open_world,
    )


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
        self.runner: TenantRunner | None = None

        @asynccontextmanager
        async def _lifespan(_app: Any) -> Any:
            await self.startup()
            try:
                yield {}
            finally:
                await self.shutdown()

        self.app = FastMCP(
            name,
            instructions=(
                "job_ftch multi-tenant vacancy pipeline. "
                "Use run_pipeline / search_jobs / list_tenants to operate. "
                "LLM steps use JOB_FTCH_OPENAI_BASE_URL (point at CLIProxyAPI "
                "or another OpenAI-compatible gateway for subscription models)."
            ),
            lifespan=_lifespan,
        )
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
        transport: TransportName = "stdio",
        host: str = "127.0.0.1",
        port: int = 8000,
    ) -> None:
        # Lifespan owns TenantRunner startup/shutdown for the process.
        # stdio rejects host/port kwargs in FastMCP 3.x.
        if transport == "stdio":
            self.app.run(transport=transport)
            return
        self.app.run(transport=transport, host=host, port=port)

    def _register_surface(self) -> None:
        """Register bot-parity product tools; expand with ops/admin surface."""
        surface = resolve_surface()
        ro = _tool_annotations(read_only=True, idempotent=True)
        write = _tool_annotations(read_only=False, open_world=True)
        destructive = _tool_annotations(destructive=True, open_world=True)

        def tool(**kwargs: Any) -> Any:
            annotations = kwargs.pop("annotations", None)
            if annotations is None:
                return self.app.tool
            return self.app.tool(annotations=annotations)

        # --- core product loop (Telegram bot parity) ----------------------

        @tool(annotations=ro)
        async def list_tenants() -> list[dict[str, Any]]:
            """List loaded tenants (like /tenant)."""
            tenants = await self._require_runner().list_tenants()
            return [tenant.model_dump(mode="json") for tenant in tenants]

        @tool(annotations=ro)
        async def get_status(tenant_id: str) -> dict[str, Any] | None:
            """Latest run status for a tenant (like /status)."""
            summary = await self._require_runner().get_status(tenant_id)
            return None if summary is None else summary.as_dict()

        @tool(annotations=ro)
        async def list_sources(tenant_id: str) -> list[dict[str, Any]]:
            """List sources with health/status embedded (like /sources)."""
            return await self._require_runner().list_sources(tenant_id)

        @tool(annotations=write)
        async def upsert_source(
            tenant_id: str,
            link: str,
            source_type: str | None = None,
            limit: int = 100,
            replace_source_id: str | None = None,
        ) -> dict[str, Any]:
            """Add a source, or change one by replace_source_id (disable old + add new)."""
            return await upsert_source_action(
                self._require_runner(),
                tenant_id=tenant_id,
                link=link,
                source_type=source_type,
                limit=limit,
                replace_source_id=replace_source_id,
            )

        @tool(annotations=write)
        async def set_source_enabled(
            tenant_id: str,
            source_id: str,
            enabled: bool = True,
        ) -> dict[str, Any]:
            """Enable or disable a source (bot toggle)."""
            return await self._require_runner().set_source_enabled(
                tenant_id, source_id, enabled=enabled
            )

        @tool(annotations=write)
        async def add_shot(
            tenant_id: str,
            polarity: str,
            kind: str,
            text: str | None = None,
            texts: list[str] | None = None,
            user_id: str = "mcp",
            profile_id: str | None = None,
        ) -> dict[str, Any]:
            """Add positive/negative resume or job shot(s) (like /positive, /negative_job).

            polarity: positive|negative
            kind: resume|job
            Provide text and/or texts[] for batch.
            """
            if polarity not in {"positive", "negative"}:
                msg = "polarity must be 'positive' or 'negative'"
                raise ValueError(msg)
            if kind not in {"resume", "job"}:
                msg = "kind must be 'resume' or 'job'"
                raise ValueError(msg)
            return await add_shots(
                self._require_runner(),
                tenant_id=tenant_id,
                user_id=user_id,
                polarity=polarity,  # type: ignore[arg-type]
                kind=kind,  # type: ignore[arg-type]
                text=text,
                texts=texts,
                profile_id=profile_id,
            )

        @tool(annotations=ro)
        async def list_shots(
            tenant_id: str,
            user_id: str = "mcp",
            profile_id: str | None = None,
        ) -> dict[str, Any]:
            """List positive/negative resume and job shots (like /examples)."""
            return await list_shots_action(
                self._require_runner(),
                tenant_id=tenant_id,
                user_id=user_id,
                profile_id=profile_id,
            )

        @tool(annotations=write)
        async def remove_shot(
            tenant_id: str,
            polarity: str,
            kind: str,
            index: int,
            user_id: str = "mcp",
            profile_id: str | None = None,
        ) -> dict[str, Any]:
            """Remove one shot by polarity/kind/index (bot examples delete)."""
            if polarity not in {"positive", "negative"}:
                msg = "polarity must be 'positive' or 'negative'"
                raise ValueError(msg)
            if kind not in {"resume", "job"}:
                msg = "kind must be 'resume' or 'job'"
                raise ValueError(msg)
            return await remove_shot_action(
                self._require_runner(),
                tenant_id=tenant_id,
                user_id=user_id,
                polarity=polarity,  # type: ignore[arg-type]
                kind=kind,  # type: ignore[arg-type]
                index=index,
                profile_id=profile_id,
            )

        @tool(annotations=write)
        async def run_pipeline(tenant_id: str) -> dict[str, Any]:
            """Run ingest/decision pipeline for one tenant (like /run)."""
            summary = await self._require_runner().run_tenant(tenant_id)
            return summary.as_dict()

        @tool(annotations=ro)
        async def search_jobs(
            query: str = "",
            tenant_id: str | None = None,
            user_id: str | None = None,
            limit: int = 20,
            company: str | None = None,
            location: str | None = None,
            work_mode: str | None = None,
            language: str | None = None,
            source_name: str | None = None,
            min_score: float | None = None,
            routing_decision: str | None = None,
        ) -> list[dict[str, Any]]:
            """Search ACCEPT catalog vacancies (JobGroups). Not REVIEW/REJECTED.

            For borderline or dropped items use list_review_jobs / list_rejected.
            Filters are applied after FTS/catalog search. Over-fetch when filters set.
            """
            limit = min(max(limit, 1), 100)
            has_filters = any(
                [
                    company,
                    location,
                    work_mode,
                    language,
                    source_name,
                    min_score is not None,
                    routing_decision,
                ]
            )
            fetch_limit = min(100, limit * 5 if has_filters else limit)
            groups = await self._require_runner().search_jobs(
                query or "",
                tenant_id=tenant_id,
                user_id=user_id,
                limit=fetch_limit,
            )
            if not has_filters:
                return [group.model_dump(mode="json") for group in groups[:limit]]
            return filter_job_groups(
                groups,
                limit=limit,
                company=company,
                location=location,
                work_mode=work_mode,
                language=language,
                source_name=source_name,
                min_score=min_score,
                routing_decision=routing_decision,
            )

        @tool(annotations=ro)
        async def list_review_jobs(
            tenant_id: str,
            run_id: str | None = None,
            limit: int = 50,
            source_name: str | None = None,
        ) -> dict[str, Any]:
            """List compact REVIEW outcomes for a tenant (requires review_output.backend=store)."""
            limit = min(max(limit, 1), 200)
            return await self._require_runner().list_review_jobs(
                tenant_id,
                run_id=run_id,
                limit=limit,
                source_name=source_name,
            )

        @tool(annotations=ro)
        async def list_rejected(
            tenant_id: str,
            run_id: str | None = None,
            limit: int = 50,
            outcome: str | None = None,
            reason: str | None = None,
            source_name: str | None = None,
        ) -> dict[str, Any]:
            """List compact REJECTED outcomes (requires rejected_output.backend=store).

            outcome: dropped|failed|quarantined. reason e.g. policy_reject.
            """
            limit = min(max(limit, 1), 200)
            return await self._require_runner().list_rejected(
                tenant_id,
                run_id=run_id,
                limit=limit,
                outcome=outcome,
                reason=reason,
                source_name=source_name,
            )

        @tool(annotations=ro)
        async def llm_backend_health() -> dict[str, Any]:
            """Probe OpenAI-compatible LLM gateway (CLIProxy). No generation."""
            return await self._probe_llm_backend()

        @tool(annotations=destructive)
        async def clear_history(tenant_id: str) -> dict[str, Any]:
            """Clear run history / dedup / jobs so next /run is fresh (like /clear)."""
            runner = self._require_runner()
            try:
                counts = await runner.clear_run_data(tenant_id)
                return {"tenant_id": tenant_id, "mode": "clear_run_data", "counts": counts}
            except RuntimeError as exc:
                # Multi-tenant runners restrict clear_run_data; fall back to store reset.
                await runner.reset_tenant(tenant_id)
                return {
                    "tenant_id": tenant_id,
                    "mode": "reset_tenant",
                    "note": str(exc),
                }

        # Compat aliases used by earlier agents / docs
        @tool(annotations=write)
        async def add_source(
            tenant_id: str,
            link: str,
            source_type: str | None = None,
            limit: int = 100,
        ) -> dict[str, Any]:
            """Alias of upsert_source without replace (add only)."""
            result = await upsert_source_action(
                self._require_runner(),
                tenant_id=tenant_id,
                link=link,
                source_type=source_type,
                limit=limit,
            )
            return cast("dict[str, Any]", result["source"])

        @tool(annotations=destructive)
        async def disable_source(tenant_id: str, source_id: str) -> dict[str, Any]:
            """Alias of set_source_enabled(enabled=false)."""
            return await self._require_runner().set_source_enabled(
                tenant_id, source_id, enabled=False
            )

        if surface in {"ops", "admin"}:

            @tool(annotations=write)
            async def run_all_pipelines() -> list[dict[str, Any]]:
                """Run pipeline for every tenant (ops)."""
                summaries = await self._require_runner().run_all()
                return [summary.as_dict() for summary in summaries]

            @tool(annotations=ro)
            async def list_source_health(tenant_id: str) -> list[dict[str, Any]]:
                """Per-source health only (prefer list_sources)."""
                return await self._require_runner().list_source_health(tenant_id)

            @tool(annotations=ro)
            async def list_runs(
                tenant_id: str | None = None, limit: int = 20
            ) -> list[dict[str, Any]]:
                """Recent pipeline runs."""
                summaries = await self._require_runner().list_runs(tenant_id=tenant_id, limit=limit)
                return [summary.as_dict() for summary in summaries]

            @tool(annotations=ro)
            async def get_run(run_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
                """One run by id."""
                summary = await self._require_runner().get_run(run_id, tenant_id=tenant_id)
                return None if summary is None else summary.as_dict()

            @tool(annotations=ro)
            async def get_job(job_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
                """One job by id."""
                job = await self._require_runner().get_job(job_id, tenant_id=tenant_id)
                return None if job is None else job.model_dump(mode="json")

            @tool(annotations=ro)
            async def get_job_lineage(
                job_id: str,
                tenant_id: str | None = None,
            ) -> dict[str, Any] | None:
                """Job lineage (debug)."""
                lineage = await self._require_runner().get_job_lineage(job_id, tenant_id=tenant_id)
                return None if lineage is None else lineage.model_dump(mode="json")

        if surface == "admin":

            @tool(annotations=ro)
            async def list_profiles(tenant_id: str, user_id: str) -> list[dict[str, Any]]:
                """List candidate profiles (admin). Prefer list_shots for examples."""
                return await self._require_runner().list_candidate_profiles(tenant_id, user_id)

            @tool(annotations=write)
            async def save_profile(
                tenant_id: str,
                user_id: str,
                profile_id: str,
                summary: str,
            ) -> dict[str, Any]:
                """Create/update profile metadata (admin). Prefer add_shot for examples."""
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

            @tool(annotations=write)
            async def activate_profile(
                tenant_id: str,
                user_id: str,
                profile_id: str,
            ) -> dict[str, Any]:
                """Activate a profile (admin)."""
                return await self._require_runner().set_active_candidate_profile(
                    tenant_id,
                    user_id,
                    profile_id,
                )

            @tool(annotations=destructive)
            async def reset_tenant(tenant_id: str) -> None:
                """Full tenant namespace reset (admin destructive). Prefer clear_history."""
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

    async def _probe_llm_backend(self) -> dict[str, Any]:
        return await probe_llm_backend(self.base_settings)

    def _require_runner(self) -> TenantRunner:
        if self.runner is None:
            msg = "TenantMCPServer.startup() must run before using MCP tools."
            raise RuntimeError(msg)
        return self.runner


async def probe_llm_backend(settings: Settings) -> dict[str, Any]:
    """Probe OpenAI-compatible LLM gateway without generation calls.

    Pure adapter helper: settings in, status dict out. No TenantRunner needed.
    Secrets are never returned.
    """
    backend = settings.llm_backend
    base_url = settings.openai_base_url
    model = settings.openai_model
    result: dict[str, Any] = {
        "ok": False,
        "llm_backend": backend,
        "openai_base_url": base_url,
        "openai_model": model,
        "reachable": False,
        "models_sample": [],
        "error": None,
    }
    if backend != "openai":
        result["ok"] = True
        result["error"] = f"llm_backend={backend!r} does not use OpenAI-compatible HTTP"
        return result
    if not base_url:
        result["error"] = "JOB_FTCH_OPENAI_BASE_URL is empty"
        return result

    root = base_url if base_url.endswith("/") else base_url + "/"
    models_url = urljoin(root, "models")
    headers: dict[str, str] = {}
    if settings.openai_api_key is not None:
        headers["Authorization"] = f"Bearer {settings.openai_api_key.get_secret_value()}"

    try:
        import httpx
    except ImportError:
        result["error"] = "httpx is required to probe the LLM gateway"
        return result

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(models_url, headers=headers)
        if response.status_code >= 400:
            result["error"] = f"HTTP {response.status_code} from {models_url}"
            return result
        payload = response.json()
        ids: list[str] = []
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, list):
            for item in data[:20]:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    ids.append(item["id"])
        result["reachable"] = True
        result["models_sample"] = ids
        result["ok"] = True
        if model and ids and model not in ids:
            result["error"] = (
                f"configured model {model!r} not in gateway /models sample "
                f"(first {len(ids)} ids listed)"
            )
            # Still reachable; warn only.
            result["ok"] = True
        return result
    except Exception as exc:  # noqa: BLE001 - surface probe failure to MCP client
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result


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
