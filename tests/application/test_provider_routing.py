from __future__ import annotations

import pytest

from job_ftch.application.provider_routing import (
    UnavailableLLMProvider,
    build_llm_bindings,
)
from job_ftch.config import Settings


def test_default_bindings_keep_captcha_on_the_named_clipproxy_model() -> None:
    settings = Settings.model_validate({"llm_backend": "heuristic"})

    assert settings.llm_bindings["captcha_image"].provider == "cliproxy_captcha"
    assert settings.llm_bindings["captcha_image"].model == "gemini-3.8-flash-high"
    assert settings.llm_bindings["extraction"].provider == "openai_main"


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
