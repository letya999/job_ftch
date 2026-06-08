from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from job_ftch.config import Settings
from job_ftch.infrastructure.sources.career_site import _RetryingHttpClient
from job_ftch.infrastructure.sources.telegram import _build_telegram_client


class FlakyHttpClient:
    def __init__(self) -> None:
        self.calls = 0

    async def __aenter__(self) -> FlakyHttpClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb

    async def get(self, url: str, *, follow_redirects: bool = False) -> httpx.Response:
        del follow_redirects
        self.calls += 1
        if self.calls == 1:
            raise httpx.ConnectTimeout("timed out", request=httpx.Request("GET", url))
        return httpx.Response(200, text="ok", request=httpx.Request("GET", url))


@pytest.mark.asyncio
async def test_retrying_http_client_retries_transient_errors() -> None:
    client = _RetryingHttpClient(
        FlakyHttpClient(),  # type: ignore[arg-type]
        max_retries=2,
        retry_delay_seconds=0.0,
    )

    response = await client.get("https://example.com/jobs")

    assert response.status_code == 200
    assert client._client.calls == 2  # type: ignore[attr-defined]


def test_build_telegram_client_applies_retry_and_timeout_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeTelegramClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured["args"] = args
            captured["kwargs"] = kwargs
            self.flood_sleep_threshold = 0

    monkeypatch.setitem(sys.modules, "telethon", SimpleNamespace(TelegramClient=FakeTelegramClient))
    settings = Settings.model_validate(
        {
            "source_backend": "telegram_channel",
            "telegram_api_id": 123,
            "telegram_api_hash": "hash",
            "telegram_entity": "ai_jobs",
            "telegram_session_path": str(Path(".runtime/test.session")),
            "telegram_timeout_seconds": 12.5,
            "telegram_request_retries": 4,
            "telegram_connection_retries": 6,
            "telegram_retry_delay_seconds": 2.0,
            "telegram_flood_sleep_threshold_seconds": 90,
        }
    )

    client = _build_telegram_client(settings)

    assert isinstance(client, FakeTelegramClient)
    assert captured["kwargs"] == {
        "timeout": 12.5,
        "request_retries": 4,
        "connection_retries": 6,
        "retry_delay": 2.0,
    }
    assert client.flood_sleep_threshold == 90
