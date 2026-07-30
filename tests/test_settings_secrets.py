"""Tests for SecretStr policy on sensitive Settings fields (ADR-035)."""

from __future__ import annotations


def test_settings_secret_fields_are_secret_str() -> None:
    """The 5 sensitive fields are typed SecretStr, not plain str."""
    from job_ftch.config import Settings

    fields = (
        "openai_api_key",
        "telegram_api_hash",
        "telegram_proxy_password",
        "langfuse_secret_key",
        "qdrant_api_key",
    )
    for name in fields:
        annotation = Settings.model_fields[name].annotation
        # annotation may be a string under from __future__ import annotations
        assert annotation is not None
        assert "SecretStr" in str(annotation), f"{name} should be typed SecretStr, got {annotation}"


def test_secret_str_masks_in_repr_and_str() -> None:
    """repr/str of Settings does not leak the raw secret value."""
    from job_ftch.config import Settings

    sentinel = "sk-test-1234567890ABCDEF"
    settings = Settings.model_validate({"openai_api_key": sentinel, "llm_backend": "heuristic"})
    rendered = repr(settings)
    assert sentinel not in rendered, f"openai_api_key leaked via repr: {rendered!r}"
    assert "**********" in rendered or "SecretStr" in rendered


def test_secret_str_get_secret_value_returns_raw() -> None:
    from job_ftch.config import Settings

    sentinel = "live-test-key-xyz"
    settings = Settings.model_validate({"openai_api_key": sentinel, "llm_backend": "heuristic"})
    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == sentinel


def test_secret_str_model_dump_excludes_secrets_by_default() -> None:
    """model_dump() of a secret field should return SecretStr, not the raw value.

    Pydantic's SecretStr makes the secret a real object whose __str__ is masked;
    json-serialising it via model_dump(mode='json') therefore does not leak.
    """
    import json

    from job_ftch.config import Settings

    sentinel = "sk-another-secret"
    settings = Settings.model_validate({"openai_api_key": sentinel, "llm_backend": "heuristic"})
    dumped = settings.model_dump(mode="json")
    # When mode='json', SecretStr is serialised to its masked representation.
    assert sentinel not in json.dumps(dumped)


def test_secret_str_strip_optional_secrets_handles_empty_string() -> None:
    """Empty string is normalised to None (consistent with str field behaviour)."""
    from job_ftch.config import Settings

    settings = Settings.model_validate(
        {
            "openai_api_key": "",
            "telegram_api_hash": "  ",
            "llm_backend": "heuristic",
            "embedding_provider": "none",
        }
    )
    assert settings.openai_api_key is None
    assert settings.telegram_api_hash is None
