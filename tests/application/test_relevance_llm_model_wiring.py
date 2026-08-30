"""Relevance judge must use ``relevance_llm_model``, not only ``openai_model``.

Regression: local CLIProxy runs set JOB_FTCH_OPENAI_MODEL to a gateway id
(e.g. gpt-5.4-mini) but left relevance_llm_model at the OpenAI default
gpt-4.1-mini. TenantRunner builds a separate LLM for the relevance judge from
relevance_llm_model; when that id is missing on CLIProxy every judge call
returns HTTP 400 and decisions DEFER with llm_relevance_unavailable.
"""

from __future__ import annotations

from job_ftch.config import Settings


def test_relevance_llm_settings_override_openai_model() -> None:
    """Mirror tenant_runner wiring: relevance provider gets relevance_llm_model."""
    settings = Settings(
        llm_backend="openai",
        openai_api_key="sk-test",  # pragma: allowlist secret
        openai_model="gpt-5.4-mini",
        relevance_llm_model="gpt-4.1-mini",
        openai_base_url="http://127.0.0.1:8317/v1",
        tracing_enabled=False,
        openobserve_enabled=False,
    )
    relevance_settings = settings.model_copy(update={"openai_model": settings.relevance_llm_model})
    assert settings.openai_model == "gpt-5.4-mini"
    assert relevance_settings.openai_model == "gpt-4.1-mini"
    assert relevance_settings.openai_base_url == settings.openai_base_url
    assert relevance_settings.openai_api_key == settings.openai_api_key


def test_cliproxy_local_profile_should_align_relevance_and_openai_models() -> None:
    """When both point at the same gateway id, judge and extract share catalog."""
    settings = Settings(
        llm_backend="openai",
        openai_api_key="cliproxy-local-key",  # pragma: allowlist secret
        openai_model="gpt-5.4-mini",
        relevance_llm_model="gpt-5.4-mini",
        openai_base_url="http://127.0.0.1:8317/v1",
        tracing_enabled=False,
        openobserve_enabled=False,
    )
    relevance_settings = settings.model_copy(update={"openai_model": settings.relevance_llm_model})
    assert relevance_settings.openai_model == settings.openai_model
