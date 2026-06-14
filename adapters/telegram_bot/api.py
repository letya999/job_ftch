"""FastAPI webhook bridge for the Telegram bot adapter using aiogram 3.x."""
# mypy: disable-error-code="untyped-decorator"

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from adapters.telegram_bot.config import load_bot_config
from adapters.telegram_bot.main import build_bot, build_dispatcher
from job_ftch.application.profile_inputs import build_candidate_profile_from_payload
from job_ftch.application.source_inputs import build_source_spec_from_input
from job_ftch.application.tenant_loader import load_tenants
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.config import Settings, get_settings
from job_ftch.domain import ManagedCandidateProfile
from job_ftch.infrastructure.auth.env_auth import EnvAuthProvider

logger = structlog.get_logger(__name__)


def create_app(
    *,
    configs_dir: str | Path | None = None,
    base_settings: Settings | None = None,
    runner: TenantRunner | None = None,
    embedding_provider: Any | None = None,
    reranker: Any | None = None,
) -> Any:
    """Create FastAPI application with aiogram 3.x webhook integration."""
    try:
        from fastapi import FastAPI, Header, HTTPException
    except ImportError as exc:
        msg = "FastAPI bridge requires the 'api' extra: pip install job-ftch[api]"
        raise ImportError(msg) from exc

    try:
        from aiogram.webhook.fastapi import SimpleRequestHandler, setup_application
    except ImportError as exc:
        msg = "aiogram 3.x is required: pip install aiogram>=3.7"
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
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # aiogram 3.x setup
    bot = build_bot(bot_config)
    dp = build_dispatcher(
        runner=runner,
        config=bot_config,
        embedding_provider=embedding_provider,
        reranker=reranker,
    )

    # Register webhook handler
    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=bot_config.secret_token,
    )
    webhook_handler.register(app, path="/webhook/telegram")

    # Setup aiogram with FastAPI (handles startup/shutdown events)
    setup_application(app, dp, bot=bot)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "tenant_count": len(runner.tenant_ids())}

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
        summary = await runner.run_tenant(str(tenant_id))
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

