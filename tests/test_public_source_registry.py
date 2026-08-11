"""Public-safe live source registry: sanitizer, runtime truth, public API."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from job_ftch.adapters.telegram_bot.public_sources import (
    PublicRegistryCache,
    build_public_sources_response,
    mount_public_source_routes,
)
from job_ftch.application.public_source_registry import (
    DEFAULT_PUBLIC_TENANT_ALLOWLIST,
    assert_public_safe_payload,
    build_public_source_registry,
    is_public_tenant,
    list_public_sources_for_runner,
    public_registry_error,
    sanitize_source_listing,
)
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.config import Settings
from job_ftch.domain import TenantConfig
from job_ftch.domain.source_spec import CareerSiteSpec, LocalFixtureSpec, TelegramChannelSpec


def _private_listing() -> dict[str, Any]:
    return {
        "source_id": "career_site:example_com_jobs",
        "source_kind": "career_site",
        "source_name": "example_com_jobs",
        "locator": "https://example.com/jobs",
        "origin": "runtime",
        "enabled": True,
        "status": "ok",
        "degraded": False,
        "last_success_at": "2026-08-01T12:00:00+00:00",
        "last_run_at": "2026-08-01T12:05:00+00:00",
        "last_error": None,
        "spec": {
            "type": "career_site",
            "url": "https://example.com/jobs",
            "monitor": "auto",
            "monitor_config": {"proxy": "redacted-internal-proxy"},
            "scraper_config": {"private_marker": "redacted-scraper-marker"},
            "auth_source_id": "vault://career",
        },
        "assessment": {
            "status": "assessed",
            "evidence": [{"details": {"cookie": "session=abc"}}],
            "recommended_monitors": ["sitemap"],
        },
        "requirements": {
            "browser_required": False,
            "browser_reason": None,
            "browser_setup_hint": "Requires Playwright",
        },
        "added_by": "123456789",
        "added_via": "telegram",
        "input_value": "https://example.com/jobs?token=secret",
        "user_id": "u-private",
        "tenant_id": "ai_jobs",
    }


def test_is_public_tenant_allowlist() -> None:
    assert is_public_tenant("ai_jobs") is True
    assert is_public_tenant("other") is False
    assert is_public_tenant("ai_jobs", allowlist=frozenset({"demo"})) is False
    assert "ai_jobs" in DEFAULT_PUBLIC_TENANT_ALLOWLIST


def test_sanitize_drops_sensitive_fields_and_keeps_public_url() -> None:
    entry = sanitize_source_listing(_private_listing())
    assert entry is not None
    payload = entry.model_dump(mode="json")
    assert_public_safe_payload(payload)
    assert payload["source_id"] == "career_site:example_com_jobs"
    assert payload["kind"] == "career_site"
    assert payload["public_url"] == "https://example.com/jobs"
    assert payload["enabled"] is True
    assert payload["status"] == "enabled"
    assert payload["parser_route_summary"] == "monitor=sitemap"
    assert "spec" not in payload
    assert "assessment" not in payload
    assert "proxy" not in str(payload)
    assert "redacted-scraper-marker" not in str(payload)
    assert "123456789" not in str(payload)


def test_sanitize_redacts_private_telegram_and_omits_fixture() -> None:
    private_tg = {
        "source_id": "telegram_group:_1001234567890",
        "source_kind": "telegram_group",
        "source_name": "_1001234567890",
        "locator": "-1001234567890",
        "enabled": True,
        "status": "ok",
        "spec": {"type": "telegram_group", "entity": "-1001234567890"},
    }
    entry = sanitize_source_listing(private_tg)
    assert entry is not None
    assert entry.public_handle is None
    assert entry.public_url is None
    assert entry.source_id.startswith("telegram_group:private_")
    assert "-1001234567890" not in entry.source_id
    assert entry.public_name == "telegram group"

    public_tg = {
        "source_id": "telegram_channel:remote_ai_jobs",
        "source_kind": "telegram_channel",
        "source_name": "remote_ai_jobs",
        "locator": "@remote_ai_jobs",
        "enabled": True,
        "status": "pending",
        "spec": {"type": "telegram_channel", "entity": "@remote_ai_jobs"},
    }
    public_entry = sanitize_source_listing(public_tg)
    assert public_entry is not None
    assert public_entry.public_handle == "@remote_ai_jobs"
    assert public_entry.public_url == "https://t.me/remote_ai_jobs"
    assert public_entry.status == "candidate"

    fixture = {
        "source_id": "local_fixture:sample",
        "source_kind": "local_fixture",
        "source_name": "sample",
        "locator": "fixtures/sources/ai_jobs.json",
        "enabled": True,
        "status": "ok",
        "spec": {"type": "local_fixture", "path": "fixtures/sources/ai_jobs.json"},
    }
    assert sanitize_source_listing(fixture) is None


def test_sanitize_failure_reason_redacts_secrets_and_paths() -> None:
    listing = {
        "source_id": "career_site:x",
        "source_kind": "career_site",
        "source_name": "x",
        "locator": "https://example.com/x",
        "enabled": True,
        "status": "failing",
        "degraded": True,
        "last_error": "token=abc123secret failed at C:\\Users\\User\\.runtime\\trace.html",
    }
    entry = sanitize_source_listing(listing)
    assert entry is not None
    assert entry.status == "degraded"
    assert entry.public_failure_reason == "redacted"


def test_registry_envelope_has_timestamp_and_count() -> None:
    registry = build_public_source_registry(
        tenant_slug="ai_jobs",
        listings=[_private_listing()],
        generated_at=datetime(2026, 8, 11, tzinfo=UTC),
    )
    payload = registry.model_dump(mode="json")
    assert_public_safe_payload(payload)
    assert payload["tenant_slug"] == "ai_jobs"
    assert payload["source_count"] == 1
    assert payload["generated_at"].startswith("2026-08-11")
    assert payload["status"] == "ok"
    assert payload["stale"] is False


def test_error_envelope_does_not_use_fixture_data() -> None:
    registry = public_registry_error(
        tenant_slug="ai_jobs",
        message="runtime source listing failed",
        status="error",
    )
    payload = registry.model_dump(mode="json")
    assert payload["source_count"] == 0
    assert payload["sources"] == []
    assert payload["status"] == "error"
    assert "fixture" not in (payload["error"] or "").casefold()
    assert_public_safe_payload(payload)


@pytest.mark.asyncio
async def test_list_public_sources_reflects_runtime_add_and_disable(
    tmp_path: Path,
) -> None:
    settings = Settings(
        store_backend="memory",
        job_group_store_backend="memory",
        search_backend="sqlite",
        embedding_enabled=False,
        llm_backend="heuristic",
    )
    tenant = TenantConfig(
        tenant_id="ai_jobs",
        display_name="AI Jobs Public Registry",
        sources=[CareerSiteSpec(url="https://example.com/base-jobs")],
        store_backend="memory",
        job_group_store_backend="memory",
        search_backend="sqlite",
    )
    runner = TenantRunner.from_tenants([tenant], base_settings=settings)

    # Must not depend on fixtures as source of truth.
    fixture_path = Path("fixtures/sources/ai_jobs.json")
    assert fixture_path.exists()  # repo has fixtures, but registry must ignore them
    before = await runner.list_public_sources("ai_jobs")
    before_ids = {item.source_id for item in before.sources}
    assert "career_site:example_com_base_jobs" in before_ids or any(
        "example_com" in item.source_id for item in before.sources
    )
    assert all("fixtures/sources/ai_jobs" not in item.source_id for item in before.sources)
    assert before.status == "ok"

    added = await runner.add_source_spec(
        "ai_jobs",
        TelegramChannelSpec(entity="@public_registry_probe"),
        added_via="test",
        added_by="operator-secret-id",
        input_value="https://t.me/public_registry_probe?token=nope",
    )
    assert added["enabled"] is True

    after_add = await runner.list_public_sources("ai_jobs")
    public_ids = {item.source_id for item in after_add.sources}
    assert any("public_registry_probe" in source_id for source_id in public_ids)
    probe = next(item for item in after_add.sources if "public_registry_probe" in item.source_id)
    assert probe.public_handle == "@public_registry_probe"
    assert probe.enabled is True
    dumped = after_add.model_dump(mode="json")
    assert_public_safe_payload(dumped)
    assert "operator-secret-id" not in str(dumped)
    assert "token=nope" not in str(dumped)

    disabled = await runner.disable_source("ai_jobs", added["source_id"])
    assert disabled["enabled"] is False

    after_disable = await runner.list_public_sources("ai_jobs")
    probe_after = next(
        item for item in after_disable.sources if item.source_id == added["source_id"]
    )
    assert probe_after.enabled is False
    assert probe_after.status == "disabled"

    # Unknown / non-allowlisted tenants: explicit error, never fixture dump.
    private = await runner.list_public_sources("private_tenant")
    assert private.status == "error"
    assert private.source_count == 0
    assert private.sources == ()

    # Runtime-only helper path with a broken runner must not invent fixture data.
    class _Broken:
        async def list_sources(self, tenant_id: str) -> list[dict[str, Any]]:
            raise RuntimeError("store down")

    broken = await list_public_sources_for_runner(_Broken(), "ai_jobs")
    assert broken.status == "error"
    assert broken.source_count == 0
    assert broken.sources == ()
    assert "fixture" not in (broken.error or "").casefold()

    # Local fixture sources stay internal.
    fixture_tenant = TenantConfig(
        tenant_id="ai_jobs",
        display_name="Fixture Tenant",
        sources=[LocalFixtureSpec(path=str(tmp_path / "local.jsonl"))],
        store_backend="memory",
        job_group_store_backend="memory",
        search_backend="sqlite",
    )
    fixture_runner = TenantRunner.from_tenants(
        [fixture_tenant],
        base_settings=settings,
    )
    fixture_public = await fixture_runner.list_public_sources("ai_jobs")
    assert fixture_public.source_count == 0
    assert fixture_public.sources == ()


@pytest.mark.asyncio
async def test_public_http_endpoint_reads_runtime_not_fixtures() -> None:
    settings = Settings(
        store_backend="memory",
        job_group_store_backend="memory",
        search_backend="sqlite",
        embedding_enabled=False,
        llm_backend="heuristic",
    )
    tenant = TenantConfig(
        tenant_id="ai_jobs",
        display_name="AI Jobs",
        sources=[CareerSiteSpec(url="https://jobs.example.org/openings")],
        store_backend="memory",
        job_group_store_backend="memory",
        search_backend="sqlite",
    )
    runner = TenantRunner.from_tenants([tenant], base_settings=settings)
    await runner.add_source_spec(
        "ai_jobs",
        TelegramChannelSpec(entity="@http_registry_probe"),
        added_via="test",
    )

    app = FastAPI()
    cache = mount_public_source_routes(app, runner, cache_ttl_seconds=0)
    assert isinstance(cache, PublicRegistryCache)

    client = TestClient(app)
    response = client.get("/public/tenants/ai_jobs/sources.json")
    assert response.status_code == 200
    body = response.json()
    assert body["tenant_slug"] == "ai_jobs"
    assert body["status"] == "ok"
    assert body["source_count"] >= 2
    assert "generated_at" in body
    assert_public_safe_payload(body)
    ids = {item["source_id"] for item in body["sources"]}
    assert any("http_registry_probe" in item for item in ids)
    assert any("jobs_example_org" in item or "openings" in item for item in ids)

    denied = client.get("/public/tenants/secret_tenant/sources.json")
    assert denied.status_code == 404

    await runner.disable_source(
        "ai_jobs",
        next(
            item["source_id"]
            for item in (await runner.list_sources("ai_jobs"))
            if "http_registry_probe" in item["source_id"]
        ),
    )
    after = client.get("/public/tenants/ai_jobs/sources")
    assert after.status_code == 200
    probe = next(
        item for item in after.json()["sources"] if "http_registry_probe" in item["source_id"]
    )
    assert probe["enabled"] is False
    assert probe["status"] == "disabled"


@pytest.mark.asyncio
async def test_build_public_sources_response_lookup_error_for_private_tenant() -> None:
    settings = Settings(
        store_backend="memory",
        job_group_store_backend="memory",
        search_backend="sqlite",
        embedding_enabled=False,
        llm_backend="heuristic",
    )
    tenant = TenantConfig(
        tenant_id="ai_jobs",
        display_name="AI Jobs",
        sources=[],
        store_backend="memory",
        job_group_store_backend="memory",
        search_backend="sqlite",
    )
    runner = TenantRunner.from_tenants([tenant], base_settings=settings)
    with pytest.raises(LookupError):
        await build_public_sources_response(runner, "not_public")
