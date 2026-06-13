from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

import pytest

from job_ftch.adapters.telegram_bot.bot import TelegramBotConfig, TelegramBotService
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.domain import RawItem, SourceKind, TenantConfig

if TYPE_CHECKING:
    from pathlib import Path


def _write_fixture(path: Path) -> None:
    item = RawItem(
        source_kind=SourceKind.DEBUG,
        source_name="fixture",
        external_id="1",
        text="Machine Learning Engineer\nRemote\nCompany: OpenAI\nSalary: USD 120000",
        metadata={"company": "OpenAI", "title": "Machine Learning Engineer"},
    )
    path.write_text(json.dumps([item.model_dump(mode="json")]), encoding="utf-8")


class FakeSender:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        self.messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})


class FakeHTTPException(Exception):
    def __init__(self, *, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class FakeFastAPI:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.routes: dict[tuple[str, str], Any] = {}

    def get(self, path: str):  # type: ignore[no-untyped-def]
        def decorator(func):  # type: ignore[no-untyped-def]
            self.routes[("GET", path)] = func
            return func

        return decorator

    def post(self, path: str):  # type: ignore[no-untyped-def]
        def decorator(func):  # type: ignore[no-untyped-def]
            self.routes[("POST", path)] = func
            return func

        return decorator


def _build_runner(tmp_path: Path) -> TenantRunner:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": "ai_jobs",
            "display_name": "AI Jobs",
            "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
            "store_backend": "sqlite",
            "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            "job_group_store_backend": "sqlite",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
        }
    )
    return TenantRunner.from_tenants([tenant])


@pytest.mark.asyncio
async def test_webhook_bridge_handles_run_and_search_updates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        sys.modules,
        "fastapi",
        type(
            "FastAPIModule",
            (),
            {
                "FastAPI": FakeFastAPI,
                "HTTPException": FakeHTTPException,
                "Header": lambda default=None: default,
            },
        ),
    )
    runner = _build_runner(tmp_path)
    sender = FakeSender()
    service = TelegramBotService(
        runner=runner,
        sender=sender,
        config=TelegramBotConfig(
            token="token",
            secret_token="secret",
            allowed_user_ids=(1,),
            allowed_chat_ids=(100,),
            admin_user_ids=(1,),
            rate_limit_seconds=0.0,
        ),
    )

    from job_ftch.adapters.telegram_bot.api import create_app

    app = create_app(configs_dir=tmp_path / "configs", runner=runner, bot_service=service)

    webhook = app.routes[("POST", "/webhook/telegram")]
    await webhook(
        {
            "message": {
                "chat": {"id": 100},
                "from": {"id": 1},
                "text": "/run ai_jobs",
            }
        },
        "secret",
    )
    await webhook(
        {
            "message": {
                "chat": {"id": 100},
                "from": {"id": 1},
                "text": "/search machine learning ai_jobs",
            }
        },
        "secret",
    )

    assert "emitted=" in sender.messages[0]["text"]
    assert "Machine Learning Engineer" in sender.messages[1]["text"]
    assert sender.messages[1]["reply_markup"] is not None

    await runner.close()


@pytest.mark.asyncio
async def test_bot_access_control_and_status_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        sys.modules,
        "fastapi",
        type(
            "FastAPIModule",
            (),
            {
                "FastAPI": FakeFastAPI,
                "HTTPException": FakeHTTPException,
                "Header": lambda default=None: default,
            },
        ),
    )
    runner = _build_runner(tmp_path)
    sender = FakeSender()
    service = TelegramBotService(
        runner=runner,
        sender=sender,
        config=TelegramBotConfig(
            token="token",
            secret_token="secret",
            bridge_api_key="bridge-key",
            allowed_user_ids=(1,),
            allowed_chat_ids=(100,),
            admin_user_ids=(1,),
            rate_limit_seconds=0.0,
        ),
    )
    from job_ftch.adapters.telegram_bot.api import create_app

    app = create_app(configs_dir=tmp_path / "configs", runner=runner, bot_service=service)
    webhook = app.routes[("POST", "/webhook/telegram")]
    status = app.routes[("GET", "/pipeline/status/{tenant_id}")]
    sources = app.routes[("GET", "/pipeline/sources/{tenant_id}")]
    pipeline_run = app.routes[("POST", "/pipeline/run")]

    await webhook(
        {
            "message": {
                "chat": {"id": 200},
                "from": {"id": 2},
                "text": "/status ai_jobs",
            }
        },
        "secret",
    )
    summary = await pipeline_run({"tenant_id": "ai_jobs"}, "bridge-key")
    status_payload = await status("ai_jobs")
    source_payload = await sources("ai_jobs", "bridge-key")

    assert sender.messages[0]["text"] == "Access denied."
    assert summary["tenant_id"] == "ai_jobs"
    assert status_payload is not None
    assert status_payload["tenant_id"] == "ai_jobs"
    assert source_payload[0]["source_id"] == "debug:fixture"

    await runner.close()


@pytest.mark.asyncio
async def test_bot_sources_command_reports_source_health(tmp_path: Path) -> None:
    runner = _build_runner(tmp_path)
    await runner.run_tenant("ai_jobs")
    sender = FakeSender()
    service = TelegramBotService(
        runner=runner,
        sender=sender,
        config=TelegramBotConfig(
            token="token",
            allowed_user_ids=(1,),
            allowed_chat_ids=(100,),
            admin_user_ids=(1,),
            rate_limit_seconds=0.0,
        ),
    )

    try:
        await service.handle_command("/sources ai_jobs", chat_id=100, user_id=1)
        assert "fixture: healthy" in sender.messages[0]["text"]
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_webhook_real_fastapi_token_auth(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    import httpx

    from job_ftch.adapters.telegram_bot.api import create_app

    runner = _build_runner(tmp_path)
    sender = FakeSender()
    service = TelegramBotService(
        runner=runner,
        sender=sender,
        config=TelegramBotConfig(
            token="token",
            secret_token="correct-secret",
            bridge_api_key="bridge-key",
            allowed_user_ids=(1,),
            allowed_chat_ids=(100,),
            admin_user_ids=(1,),
            rate_limit_seconds=0.0,
        ),
    )
    app = create_app(runner=runner, bot_service=service)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/webhook/telegram",
            json={"message": {"chat": {"id": 100}, "from": {"id": 1}, "text": "/start"}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "correct-secret"},
        )
        assert response.status_code == 200

        response = await client.post(
            "/webhook/telegram",
            json={"message": {"chat": {"id": 100}, "from": {"id": 1}, "text": "/start"}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
        )
        assert response.status_code == 403

        response = await client.get("/jobs/search", params={"q": "python"})
        assert response.status_code == 403

        response = await client.get(
            "/jobs/search",
            params={"q": "python"},
            headers={"X-API-Key": "bridge-key"},
        )
        assert response.status_code == 200

    await runner.close()
