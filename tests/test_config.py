from __future__ import annotations

import pytest

from config import Settings, SourceBackend


def test_settings_allow_blank_optional_telegram_credentials_for_non_telegram_backends() -> None:
    settings = Settings.model_validate(
        {
            "source_backend": SourceBackend.CAREER_SITE,
            "career_site_url": "https://job-boards.greenhouse.io/clickhouse",
            "telegram_api_id": "",
            "telegram_api_hash": "",
            "telegram_entity": "",
        }
    )

    assert settings.source_backend is SourceBackend.CAREER_SITE
    assert settings.telegram_api_id is None
    assert settings.telegram_api_hash is None
    assert settings.telegram_entity is None


def test_settings_reject_disallowed_career_site_host() -> None:
    with pytest.raises(ValueError, match="career_site_url host must be one of"):
        Settings.model_validate(
            {
                "source_backend": SourceBackend.CAREER_SITE,
                "career_site_url": "https://example.com/jobs",
            }
        )
