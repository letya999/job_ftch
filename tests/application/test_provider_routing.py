from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from job_ftch.application.builder import tenant_to_settings
from job_ftch.application.provider_routing import (
    UnavailableLLMProvider,
    build_llm_bindings,
)
from job_ftch.config import Settings
from job_ftch.domain import TenantConfig


def test_default_bindings_keep_captcha_on_the_named_clipproxy_model() -> None:
    settings = Settings.model_validate({"llm_backend": "heuristic"})

    assert settings.llm_bindings["captcha_image"].provider == "cliproxy_captcha"
    assert settings.llm_bindings["captcha_image"].model == "gemini-3.8-flash-high"
    assert settings.llm_bindings["extraction"].provider == "openai_main"


def test_tenant_settings_keep_implicit_heuristic_provider() -> None:
    tenant = TenantConfig(tenant_id="test", display_name="Test")
    settings = tenant_to_settings(tenant, Settings.model_validate({"llm_backend": "heuristic"}))

    assert settings.llm_provider_profiles["openai_main"].backend == "heuristic"


def test_production_bot_routes_etl_through_cliproxy() -> None:
    config = yaml.safe_load(
        Path("job_ftch/adapters/telegram_bot/runtime.prod.yaml").read_text(encoding="utf-8")
    )
    settings = Settings.model_validate(config)

    assert settings.llm_preflight_timeout_seconds == 30.0
    for name in ("extraction", "relevance", "ontology", "presentation"):
        binding = settings.llm_bindings[name]
        assert binding.provider == "cliproxy_main"
        assert binding.model == "gemini-3.8-flash-high"
    profile = settings.llm_provider_profiles["cliproxy_main"]
    assert profile.base_url == "http://cliproxy:8317/v1"
    assert profile.credential_ref == "JOB_FTCH_CLIPROXY_API_KEY"
    assert profile.structured_mode == "json"
    assert settings.llm_bindings["captcha_image"].provider == "cliproxy_captcha"
    assert settings.llm_provider_profiles["cliproxy_captcha"].base_url == "http://cliproxy:8317/v1"
    tenant = TenantConfig(tenant_id="test", display_name="Test")
    assert (
        tenant_to_settings(tenant, settings).llm_bindings["extraction"].provider == "cliproxy_main"
    )


def test_cliproxy_json_mode_reaches_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    config = yaml.safe_load(
        Path("job_ftch/adapters/telegram_bot/runtime.prod.yaml").read_text(encoding="utf-8")
    )
    settings = Settings.model_validate(config)
    passed: list[Settings] = []

    class Provider:
        pass

    def create_provider(provider_settings: Settings) -> Provider:
        passed.append(provider_settings)
        return Provider()

    monkeypatch.setattr("job_ftch.application.registry.create_llm", create_provider)
    build_llm_bindings(settings, names=("extraction",))

    assert len(passed) == 1
    assert passed[0].llm_structured_mode == "json"


def test_unavailable_binding_is_a_safe_runtime_placeholder() -> None:
    settings = Settings.model_validate({"llm_backend": "heuristic"})
    providers = build_llm_bindings(settings, names=("extraction",))

    assert "extraction" in providers
    assert getattr(providers["extraction"], "provider_id", "") == "openai_main"


@pytest.mark.asyncio
async def test_unavailable_provider_preflight_is_nonfatal() -> None:
    provider = UnavailableLLMProvider(
        provider="openai_main", model="gpt-test", error="missing credential"
    )

    result = await provider.preflight()

    assert result.available is False
    assert result.error == "missing credential"
