"""FastAPI webhook bridge for the Telegram bot adapter using aiogram 3.x."""
# mypy: disable-error-code="untyped-decorator"

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import structlog
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from job_ftch.adapters.telegram_bot.config import load_bot_config
from job_ftch.adapters.telegram_bot.main import build_bot, build_dispatcher
from job_ftch.adapters.telegram_bot.public_jobs import mount_public_job_routes
from job_ftch.adapters.telegram_bot.public_sources import mount_public_source_routes
from job_ftch.application.profile_inputs import build_candidate_profile_from_payload
from job_ftch.application.source_inputs import build_source_spec_from_input
from job_ftch.application.tenant_loader import load_tenants
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.config import Settings, get_settings
from job_ftch.domain import ManagedCandidateProfile, SourceHealth
from job_ftch.infrastructure.auth.env_auth import EnvAuthProvider

try:
    from fastapi import Request, Response
except ImportError:  # pragma: no cover - api extra optional at import time
    Request = None  # type: ignore[misc, assignment]
    Response = None  # type: ignore[misc, assignment]

logger = structlog.get_logger(__name__)


def _parse_health_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _age_seconds(value: str | None) -> float | None:
    parsed = _parse_health_datetime(value)
    if parsed is None:
        return None
    return max((datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds(), 0.0)


def _int_state(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def _worst_status(statuses: list[str]) -> str:
    if "unhealthy" in statuses:
        return "unhealthy"
    if "degraded" in statuses:
        return "degraded"
    return "ok"


def _health_from_latest_run(item: SourceHealth, finished_at: datetime | None) -> bool:
    if finished_at is None:
        return False
    run_at = _parse_health_datetime(item.last_run_at)
    if run_at is None:
        return False
    finished = finished_at if finished_at.tzinfo is not None else finished_at.replace(tzinfo=UTC)
    return abs((run_at - finished.astimezone(UTC)).total_seconds()) <= 30 * 60


async def _catalog_source_ids(runner: TenantRunner, tenant_id: str) -> set[str]:
    list_sources = getattr(runner, "list_sources", None)
    if not callable(list_sources):
        return set()
    try:
        listed = await list_sources(tenant_id)
    except Exception:
        logger.debug("health_catalog_unavailable", tenant_id=tenant_id)
        return set()
    return {
        str(item.get("source_id"))
        for item in listed
        if isinstance(item, dict) and item.get("source_id")
    }


def _live_source_health(
    source_health: list[SourceHealth],
    *,
    catalog_ids: set[str],
    finished_at: datetime | None,
) -> list[SourceHealth]:
    if not catalog_ids and finished_at is None:
        return source_health
    live: list[SourceHealth] = []
    for item in source_health:
        if catalog_ids and item.source_id in catalog_ids:
            live.append(item)
            continue
        if _health_from_latest_run(item, finished_at):
            live.append(item)
    return live


async def _tenant_health(runner: TenantRunner, tenant_id: str) -> dict[str, Any]:
    runtime = runner.get_runtime(tenant_id)
    store = runtime.store
    try:
        summary = await runner.get_status(tenant_id)
        catalog_ids = await _catalog_source_ids(runner, tenant_id)
        source_health = _live_source_health(
            await store.list_source_health(),
            catalog_ids=catalog_ids,
            finished_at=summary.finished_at if summary is not None else None,
        )
        scheduler_state = {
            key: await store.get_run_state(key)
            for key in (
                "bot_scheduler:last_attempt_at",
                "bot_scheduler:last_success_at",
                "bot_scheduler:last_error",
                "bot_scheduler:last_publish_success_at",
                "bot_scheduler:last_publish_error",
                "bot_scheduler:last_publish_sent",
                "bot_scheduler:pending_publish_since",
            )
        }
    except Exception as exc:
        logger.warning("health_dependency_check_failed", tenant_id=tenant_id, error=str(exc))
        return {
            "tenant_id": tenant_id,
            "status": "unhealthy",
            "store": {"ok": False, "error": str(exc)},
        }

    bad_sources = [
        item
        for item in source_health
        if item.paused or item.degraded or item.status in {"failing", "paused", "degraded"}
    ]
    watch_sources = [
        item
        for item in source_health
        if item.quality_high_relevance or (item.quality_reliable and item.quality_rich)
    ]
    scheduler_error = str(scheduler_state.get("bot_scheduler:last_error") or "").strip()
    publish_error = str(scheduler_state.get("bot_scheduler:last_publish_error") or "").strip()
    status = "degraded" if bad_sources or scheduler_error or publish_error else "ok"
    last_finished = summary.finished_at.isoformat() if summary and summary.finished_at else None
    return {
        "tenant_id": tenant_id,
        "status": status,
        "store": {"ok": True},
        "last_run": {
            "source_run_id": summary.source_run_id if summary else None,
            "finished_at": last_finished,
            "finished_age_seconds": _age_seconds(last_finished),
            "failed": bool(summary and summary.failed > 0),
            "fetched": summary.fetched if summary else 0,
            "emitted": summary.emitted if summary else 0,
        },
        "sources": {
            "total": len(source_health),
            "degraded": len(bad_sources),
            "bad_source_ids": [item.source_id for item in bad_sources],
            "reliable": sum(1 for item in source_health if item.quality_reliable),
            "rich": sum(1 for item in source_health if item.quality_rich),
            "high_relevance": sum(1 for item in source_health if item.quality_high_relevance),
            "watch_source_ids": [item.source_id for item in watch_sources],
        },
        "scheduler": {
            "last_attempt_age_seconds": _age_seconds(
                scheduler_state.get("bot_scheduler:last_attempt_at")
            ),
            "last_success_age_seconds": _age_seconds(
                scheduler_state.get("bot_scheduler:last_success_at")
            ),
            "last_error": scheduler_error or None,
        },
        "publish": {
            "last_success_age_seconds": _age_seconds(
                scheduler_state.get("bot_scheduler:last_publish_success_at")
            ),
            "last_error": publish_error or None,
            "last_sent": _int_state(scheduler_state.get("bot_scheduler:last_publish_sent")),
            "pending_since": scheduler_state.get("bot_scheduler:pending_publish_since") or None,
            "pending_age_seconds": _age_seconds(
                scheduler_state.get("bot_scheduler:pending_publish_since")
            ),
        },
    }


def create_app(
    *,
    configs_dir: str | Path | None = None,
    base_settings: Settings | None = None,
    runner: TenantRunner | None = None,
) -> Any:
    """Create FastAPI application with aiogram 3.x webhook integration."""
    try:
        from fastapi import FastAPI, Header, HTTPException
    except ImportError as exc:
        msg = "FastAPI bridge requires the 'api' extra: pip install job-ftch[api]"
        raise ImportError(msg) from exc

    settings = base_settings or get_settings()
    resolved_configs_dir = Path(configs_dir or settings.configs_dir or "")
    if runner is None:
        if not resolved_configs_dir:
            msg = "configs_dir is required for the Telegram bot API bridge."
            raise ValueError(msg)
        runner = TenantRunner.from_tenants(
            load_tenants(resolved_configs_dir), base_settings=settings
        )

    auth = EnvAuthProvider()
    bot_config = load_bot_config(auth)

    if not bot_config.secret_token:
        raise RuntimeError(
            "TELEGRAM_SECRET_TOKEN must be set; refusing to start without authentication"
        )

    # Initialize rate limiter
    limiter = Limiter(key_func=get_remote_address)
    app = FastAPI(title="job_ftch telegram bridge")
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, cast("Any", _rate_limit_exceeded_handler))

    # aiogram 3.x setup
    bot = build_bot(bot_config)
    dp = build_dispatcher(
        runner=runner,
        config=bot_config,
    )

    @app.post("/webhook/telegram", response_model=None)
    async def telegram_webhook(request: Request) -> Response | dict[str, Any]:
        supplied_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not bot_config.secret_token or not hmac.compare_digest(
            supplied_token, bot_config.secret_token
        ):
            raise HTTPException(status_code=403, detail="Invalid Telegram webhook token.")
        from aiogram.types import Update

        update = Update.model_validate(await request.json())
        result: Any = await dp.feed_webhook_update(bot, update)
        if result is None:
            return Response(status_code=200)
        return cast("dict[str, Any]", result.model_dump(by_alias=True, exclude_none=True))

    @app.get("/health/live")
    async def health_live() -> dict[str, Any]:
        return {"status": "ok"}

    @app.get("/health")
    async def health() -> dict[str, Any]:
        tenants: list[dict[str, Any]] = []
        for tenant_id in runner.tenant_ids():
            tenants.append(await _tenant_health(runner, tenant_id))
        return {
            "status": _worst_status([str(item["status"]) for item in tenants]),
            "tenant_count": len(tenants),
            "tenants": tenants,
        }

    # Public-safe runtime source registry (no API key; allowlisted tenants only).
    mount_public_source_routes(app, runner, limiter=limiter)
    mount_public_job_routes(app, runner, limiter=limiter)

    @app.post("/pipeline/run")
    @limiter.limit("5/minute")
    async def pipeline_run(
        request: Any,
        payload: dict[str, Any] | None = None,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        expected_key = bot_config.bridge_api_key
        if not expected_key or not hmac.compare_digest(x_api_key or "", expected_key):
            raise HTTPException(status_code=403, detail="Invalid bridge API key.")
        tenant_id = None if payload is None else payload.get("tenant_id")
        if tenant_id is None:
            summaries = await runner.run_all()
            return {"runs": [summary.as_dict() for summary in summaries]}
        user_id = None if payload is None else payload.get("user_id")
        summary = await runner.run_tenant(
            str(tenant_id),
            user_id=str(user_id) if user_id is not None else None,
        )
        return summary.as_dict()

    @app.get("/pipeline/status/{tenant_id}")
    @limiter.limit("10/minute")
    async def pipeline_status(
        request: Any,
        tenant_id: str,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any] | None:
        expected_key = bot_config.bridge_api_key
        if not expected_key or not hmac.compare_digest(x_api_key or "", expected_key):
            raise HTTPException(status_code=403, detail="Invalid bridge API key.")
        summary = await runner.get_status(tenant_id)
        return None if summary is None else summary.as_dict()

    @app.get("/pipeline/browser-capabilities")
    @limiter.limit("10/minute")
    async def pipeline_browser_capabilities(
        request: Any,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Read-only browser/bypass capability inventory (no execution)."""
        del request
        expected_key = bot_config.bridge_api_key
        if not expected_key or not hmac.compare_digest(x_api_key or "", expected_key):
            raise HTTPException(status_code=403, detail="Invalid bridge API key.")
        from job_ftch.application.browser_capability_inventory import (
            inventory_to_public_dict,
        )

        return inventory_to_public_dict(runner.list_browser_capabilities())

    @app.get("/pipeline/browser-routes")
    @limiter.limit("10/minute")
    async def pipeline_browser_routes(
        request: Any,
        tenant_id: str | None = None,
        source_id: str | None = None,
        bypass: str | None = None,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Route planner diagnostics for a source/spec (read-only)."""
        del request
        expected_key = bot_config.bridge_api_key
        if not expected_key or not hmac.compare_digest(x_api_key or "", expected_key):
            raise HTTPException(status_code=403, detail="Invalid bridge API key.")
        from job_ftch.application.browser_capability_inventory import (
            explanation_to_public_dict,
        )

        explanation = await runner.explain_browser_route(
            tenant_id,
            source_id,
            bypass=bypass,
        )
        return explanation_to_public_dict(explanation)

    @app.post("/pipeline/search-sessions")
    @limiter.limit("5/minute")
    async def create_search_session(
        request: Any,
        payload: dict[str, Any],
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Create a resume-driven search session."""
        del request
        expected_key = bot_config.bridge_api_key
        if not expected_key or not hmac.compare_digest(x_api_key or "", expected_key):
            raise HTTPException(status_code=403, detail="Invalid bridge API key.")
        from job_ftch.application.search_session import session_to_public_dict

        tenant_id = str(payload.get("tenant_id") or "").strip()
        if not tenant_id:
            raise HTTPException(status_code=400, detail="tenant_id is required")
        source_scope = payload.get("source_scope")
        if source_scope is not None and not isinstance(source_scope, list):
            raise HTTPException(status_code=400, detail="source_scope must be a list of source ids")
        session = await runner.create_search_session(
            tenant_id,
            user_id=str(payload["user_id"]) if payload.get("user_id") is not None else None,
            profile_id=(
                str(payload["profile_id"]) if payload.get("profile_id") is not None else None
            ),
            source_scope=[str(item) for item in source_scope] if source_scope else None,
            max_items=payload.get("max_items"),
            max_sources=payload.get("max_sources"),
            result_limit=int(payload.get("result_limit") or 20),
        )
        return session_to_public_dict(session)

    @app.post("/pipeline/search-sessions/{session_id}/plan")
    @limiter.limit("5/minute")
    async def plan_search_session(
        request: Any,
        session_id: str,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        del request
        expected_key = bot_config.bridge_api_key
        if not expected_key or not hmac.compare_digest(x_api_key or "", expected_key):
            raise HTTPException(status_code=403, detail="Invalid bridge API key.")
        from job_ftch.application.search_session import session_to_public_dict

        session = await runner.plan_source_routes(session_id)
        return session_to_public_dict(session)

    @app.post("/pipeline/search-sessions/{session_id}/approve")
    @limiter.limit("5/minute")
    async def approve_search_session(
        request: Any,
        session_id: str,
        payload: dict[str, Any],
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        del request
        expected_key = bot_config.bridge_api_key
        if not expected_key or not hmac.compare_digest(x_api_key or "", expected_key):
            raise HTTPException(status_code=403, detail="Invalid bridge API key.")
        from job_ftch.application.search_session import session_to_public_dict

        approved_sources = payload.get("approved_source_ids")
        approved_caps = payload.get("approved_capability_ids")
        session = await runner.approve_search_session(
            session_id,
            approved_source_ids=(
                [str(item) for item in approved_sources]
                if isinstance(approved_sources, list)
                else None
            ),
            approved_capability_ids=(
                [str(item) for item in approved_caps] if isinstance(approved_caps, list) else None
            ),
            approve_all_sensitive=bool(payload.get("approve_all_sensitive")),
            note=str(payload["note"]) if payload.get("note") is not None else None,
        )
        return session_to_public_dict(session)

    @app.post("/pipeline/search-sessions/{session_id}/run")
    @limiter.limit("3/minute")
    async def run_search_session(
        request: Any,
        session_id: str,
        payload: dict[str, Any] | None = None,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        del request
        expected_key = bot_config.bridge_api_key
        if not expected_key or not hmac.compare_digest(x_api_key or "", expected_key):
            raise HTTPException(status_code=403, detail="Invalid bridge API key.")
        from job_ftch.application.search_session import session_to_public_dict

        body = payload or {}
        session = await runner.run_search_session(
            session_id,
            skip_pipeline=bool(body.get("skip_pipeline")),
        )
        return session_to_public_dict(session)

    @app.get("/pipeline/search-sessions/{session_id}")
    @limiter.limit("10/minute")
    async def get_search_session(
        request: Any,
        session_id: str,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        del request
        expected_key = bot_config.bridge_api_key
        if not expected_key or not hmac.compare_digest(x_api_key or "", expected_key):
            raise HTTPException(status_code=403, detail="Invalid bridge API key.")
        from job_ftch.application.search_session import session_to_public_dict

        session = await runner.get_search_session_status(session_id)
        return session_to_public_dict(session)

    @app.get("/pipeline/search-sessions/{session_id}/results")
    @limiter.limit("10/minute")
    async def list_search_session_results(
        request: Any,
        session_id: str,
        limit: int = 20,
        x_api_key: str | None = Header(default=None),
    ) -> list[dict[str, Any]]:
        del request
        expected_key = bot_config.bridge_api_key
        if not expected_key or not hmac.compare_digest(x_api_key or "", expected_key):
            raise HTTPException(status_code=403, detail="Invalid bridge API key.")
        refs = await runner.list_search_results(session_id, limit=limit)
        return [ref.model_dump(mode="json") for ref in refs]

    @app.get("/pipeline/search-sessions/{session_id}/explain")
    @limiter.limit("10/minute")
    async def explain_search_session(
        request: Any,
        session_id: str,
        source_id: str | None = None,
        job_id: str | None = None,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        del request
        expected_key = bot_config.bridge_api_key
        if not expected_key or not hmac.compare_digest(x_api_key or "", expected_key):
            raise HTTPException(status_code=403, detail="Invalid bridge API key.")
        from job_ftch.application.search_session import explanation_to_dict

        explanation = await runner.explain_search_session(
            session_id,
            source_id=source_id,
            job_id=job_id,
        )
        return explanation_to_dict(explanation)

    @app.post("/pipeline/search-sessions/{session_id}/cancel")
    @limiter.limit("5/minute")
    async def cancel_search_session(
        request: Any,
        session_id: str,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        del request
        expected_key = bot_config.bridge_api_key
        if not expected_key or not hmac.compare_digest(x_api_key or "", expected_key):
            raise HTTPException(status_code=403, detail="Invalid bridge API key.")
        from job_ftch.application.search_session import session_to_public_dict

        session = await runner.cancel_search_session(session_id)
        return session_to_public_dict(session)

    @app.get("/pipeline/sources/{tenant_id}")
    @limiter.limit("10/minute")
    async def pipeline_sources(
        request: Any,
        tenant_id: str,
        x_api_key: str | None = Header(default=None),
    ) -> list[dict[str, Any]]:
        expected_key = bot_config.bridge_api_key
        if not expected_key or not hmac.compare_digest(x_api_key or "", expected_key):
            raise HTTPException(status_code=403, detail="Invalid bridge API key.")
        return await runner.list_sources(tenant_id)

    @app.post("/pipeline/sources/{tenant_id}")
    @limiter.limit("5/minute")
    async def add_pipeline_source(
        request: Any,
        tenant_id: str,
        payload: dict[str, Any],
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        expected_key = bot_config.bridge_api_key
        if not expected_key or not hmac.compare_digest(x_api_key or "", expected_key):
            raise HTTPException(status_code=403, detail="Invalid bridge API key.")
        try:
            spec = await build_source_spec_from_input(
                str(payload.get("link") or ""),
                auth_provider=runner.get_runtime(tenant_id).auth_provider,
                source_type=payload.get("source_type"),
                limit=int(payload.get("limit") or 100),
            )
            return await runner.add_source_spec(
                tenant_id,
                spec,
                added_via="api",
                added_by=str(payload.get("added_by")) if payload.get("added_by") else None,
                input_value=str(payload.get("link") or ""),
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/pipeline/sources/{tenant_id}/disable")
    @limiter.limit("5/minute")
    async def disable_pipeline_source(
        request: Any,
        tenant_id: str,
        payload: dict[str, Any],
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        expected_key = bot_config.bridge_api_key
        if not expected_key or not hmac.compare_digest(x_api_key or "", expected_key):
            raise HTTPException(status_code=403, detail="Invalid bridge API key.")
        source_id = payload.get("source_id")
        if not isinstance(source_id, str) or not source_id.strip():
            raise HTTPException(status_code=400, detail="source_id is required.")
        try:
            return await runner.disable_source(tenant_id, source_id.strip())
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/profiles/{tenant_id}/{user_id}")
    @limiter.limit("20/minute")
    async def list_profiles(
        request: Any,
        tenant_id: str,
        user_id: str,
        x_api_key: str | None = Header(default=None),
    ) -> list[dict[str, Any]]:
        expected_key = bot_config.bridge_api_key
        if not expected_key or not hmac.compare_digest(x_api_key or "", expected_key):
            raise HTTPException(status_code=403, detail="Invalid bridge API key.")
        return await runner.list_candidate_profiles(tenant_id, user_id)

    @app.post("/profiles/{tenant_id}/{user_id}")
    @limiter.limit("10/minute")
    async def save_profile(
        request: Any,
        tenant_id: str,
        user_id: str,
        payload: dict[str, Any],
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        expected_key = bot_config.bridge_api_key
        if not expected_key or not hmac.compare_digest(x_api_key or "", expected_key):
            raise HTTPException(status_code=403, detail="Invalid bridge API key.")
        profile_id = str(payload.get("profile_id") or "").strip()
        if not profile_id:
            raise HTTPException(status_code=400, detail="profile_id is required.")
        profile = build_candidate_profile_from_payload(
            user_id=user_id,
            profile_id=profile_id,
            payload=payload,
        )
        saved = await runner.save_candidate_profile(
            tenant_id,
            ManagedCandidateProfile(
                user_id=user_id,
                profile_id=profile_id,
                profile=profile,
                updated_at=datetime.now(UTC),
            ),
        )
        if bool(payload.get("activate", True)):
            await runner.set_active_candidate_profile(tenant_id, user_id, profile_id)
        return saved

    @app.post("/profiles/{tenant_id}/{user_id}/activate")
    @limiter.limit("10/minute")
    async def activate_profile(
        request: Any,
        tenant_id: str,
        user_id: str,
        payload: dict[str, Any],
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        expected_key = bot_config.bridge_api_key
        if not expected_key or not hmac.compare_digest(x_api_key or "", expected_key):
            raise HTTPException(status_code=403, detail="Invalid bridge API key.")
        profile_id = str(payload.get("profile_id") or "").strip()
        if not profile_id:
            raise HTTPException(status_code=400, detail="profile_id is required.")
        try:
            return await runner.set_active_candidate_profile(tenant_id, user_id, profile_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/jobs/search")
    @limiter.limit("30/minute")
    async def search_jobs(
        request: Any,
        q: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
        limit: int = 20,
        x_api_key: str | None = Header(default=None),
    ) -> list[dict[str, Any]]:
        expected_key = bot_config.bridge_api_key
        if not expected_key or not hmac.compare_digest(x_api_key or "", expected_key):
            raise HTTPException(status_code=403, detail="Invalid bridge API key.")
        limit = min(limit, 100)
        groups = await runner.search_jobs(q, tenant_id=tenant_id, user_id=user_id, limit=limit)
        return [group.model_dump(mode="json") for group in groups]

    return app
