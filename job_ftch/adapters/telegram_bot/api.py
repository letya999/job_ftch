"""FastAPI webhook bridge for the Telegram bot adapter."""
# mypy: disable-error-code="untyped-decorator"

from __future__ import annotations

from pathlib import Path
from typing import Any

from job_ftch.adapters.telegram_bot.bot import (
    HttpTelegramBotClient,
    TelegramBotService,
    load_bot_config,
)
from job_ftch.application.tenant_loader import load_tenants
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.config import Settings, get_settings
from job_ftch.infrastructure.auth.env_auth import EnvAuthProvider


def create_app(
    *,
    configs_dir: str | Path | None = None,
    base_settings: Settings | None = None,
    runner: TenantRunner | None = None,
    bot_service: TelegramBotService | None = None,
) -> Any:
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
    if bot_service is None:
        auth = EnvAuthProvider()
        bot_config = load_bot_config(auth)
        sender = HttpTelegramBotClient(bot_config.token)
        bot_service = TelegramBotService(runner=runner, sender=sender, config=bot_config)
    else:
        bot_config = bot_service.config

    app = FastAPI(title="job_ftch telegram bridge")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "tenant_count": len(runner.tenant_ids())}

    @app.post("/webhook/telegram")
    async def telegram_webhook(
        payload: dict[str, Any],
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> dict[str, bool]:
        expected = bot_config.secret_token
        if expected and x_telegram_bot_api_secret_token != expected:
            raise HTTPException(status_code=403, detail="Invalid Telegram secret token.")
        await bot_service.handle_update(payload)
        return {"ok": True}

    @app.post("/pipeline/run")
    async def pipeline_run(
        payload: dict[str, Any] | None = None,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        expected = bot_config.bridge_api_key
        if expected and x_api_key != expected:
            raise HTTPException(status_code=403, detail="Invalid bridge API key.")
        tenant_id = None if payload is None else payload.get("tenant_id")
        if tenant_id is None:
            summaries = await runner.run_all()
            return {"runs": [summary.as_dict() for summary in summaries]}
        summary = await runner.run_tenant(str(tenant_id))
        return summary.as_dict()

    @app.get("/pipeline/status/{tenant_id}")
    async def pipeline_status(tenant_id: str) -> dict[str, Any] | None:
        summary = await runner.get_status(tenant_id)
        return None if summary is None else summary.as_dict()

    @app.get("/jobs/search")
    async def search_jobs(
        q: str,
        tenant_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        groups = await runner.search_jobs(q, tenant_id=tenant_id, limit=limit)
        return [group.model_dump(mode="json") for group in groups]

    return app
