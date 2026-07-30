from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from job_ftch.config import Settings
from job_ftch.domain.source_spec import CareerSiteSpec, TelegramChannelSpec, TelegramGroupSpec
from job_ftch.infrastructure.sources.career_site import _RetryingHttpClient
from job_ftch.infrastructure.sources.career_site_source import _career_site_factory
from job_ftch.infrastructure.sources.telegram import (
    _build_telegram_channel_source_v2,
    _build_telegram_client,
    _build_telegram_group_source_v2,
)


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
            "llm_backend": "heuristic",
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
        "proxy": None,
        "timeout": 12.5,
        "request_retries": 4,
        "connection_retries": 6,
        "retry_delay": 2.0,
    }
    assert client.flood_sleep_threshold == 90


def test_career_site_factory_uses_source_default_limit_not_pipeline_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_create_source_from_spec(spec: CareerSiteSpec) -> CareerSiteSpec:
        captured["spec"] = spec
        return spec

    monkeypatch.setattr(
        "job_ftch.application.registry.create_source_from_spec",
        _fake_create_source_from_spec,
    )
    settings = Settings.model_validate(
        {
            "source_backend": "career_site",
            "llm_backend": "heuristic",
            "career_site_url": "https://example.com/jobs",
            "career_site_default_limit": 50,
            "pipeline_max_items_per_run": None,
        }
    )

    result = _career_site_factory(settings)

    assert isinstance(result, CareerSiteSpec)
    assert result.limit == 50
    assert captured["spec"] == result


def test_career_site_source_preserves_documented_unlimited_detail_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings.model_validate(
        {
            "llm_backend": "heuristic",
            "career_site_default_limit": 50,
            "career_site_default_detail_limit": 12,
        }
    )
    spec = CareerSiteSpec(url="https://example.com/jobs", source_name="jobs")

    monkeypatch.setattr("job_ftch.config.get_settings", lambda: settings)

    from job_ftch.infrastructure.sources.career_site_source import CareerSiteSource

    source = CareerSiteSource(spec, http_client=object(), auth=object())

    assert source._effective_limit() == 12


def test_telegram_channel_factory_uses_window_cap_when_cutoff_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings.model_validate(
        {
            "llm_backend": "heuristic",
            "telegram_channel_default_limit": 50,
            "telegram_window_max_messages": 1000,
        }
    )
    spec = TelegramChannelSpec(
        entity="@jobs",
        freshness_cutoff_utc="2026-07-01T00:00:00+00:00",
    )

    monkeypatch.setattr("job_ftch.config.get_settings", lambda: settings)
    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.telegram._build_client_v2",
        lambda auth_source_id, auth, **kwargs: object(),
    )

    source = _build_telegram_channel_source_v2(spec, auth=object())

    assert source._limit == 1000


def test_telegram_group_factory_uses_default_limit_without_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings.model_validate(
        {
            "llm_backend": "heuristic",
            "telegram_group_default_limit": 50,
            "telegram_window_max_messages": 1000,
        }
    )
    spec = TelegramGroupSpec(entity="@jobs")

    monkeypatch.setattr("job_ftch.config.get_settings", lambda: settings)
    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.telegram._build_client_v2",
        lambda auth_source_id, auth, **kwargs: object(),
    )

    source = _build_telegram_group_source_v2(spec, auth=object())

    assert source._limit == 50
