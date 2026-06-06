from __future__ import annotations

import pytest

from config import Settings


def test_settings_allow_blank_optional_telegram_credentials_for_non_telegram_backends() -> None:
    settings = Settings.model_validate(
        {
            "source_backend": "career_site",
            "career_site_url": "https://job-boards.greenhouse.io/clickhouse",
            "career_site_allowed_hosts": ["job-boards.greenhouse.io"],
            "telegram_api_id": "",
            "telegram_api_hash": "",
            "telegram_entity": "",
        }
    )

    assert settings.source_backend == "career_site"
    assert settings.telegram_api_id is None
    assert settings.telegram_api_hash is None
    assert settings.telegram_entity is None


def test_settings_reject_disallowed_career_site_host() -> None:
    with pytest.raises(ValueError, match="career_site_url host must be one of"):
        Settings.model_validate(
            {
                "source_backend": "career_site",
                "career_site_url": "https://example.com/jobs",
                "career_site_allowed_hosts": ["job-boards.greenhouse.io"],
            }
        )


def test_quarantine_settings_switch_output_targets() -> None:
    settings = Settings.model_validate(
        {
            "output_path": "artifacts/debug/raw_items.json",
            "quarantine_output_path": "artifacts/debug/quarantine.jsonl",
            "quarantine_output_jsonl": True,
        }
    )

    quarantine = settings.quarantine_settings()

    assert quarantine.output_path == settings.quarantine_output_path
    assert quarantine.output_jsonl is True


def test_postgres_store_backend_requires_dsn() -> None:
    with pytest.raises(ValueError, match="JOB_FTCH_POSTGRES_DSN"):
        Settings.model_validate({"store_backend": "postgres"})


def test_postgres_store_backend_allows_explicit_dsn() -> None:
    settings = Settings.model_validate(
        {
            "store_backend": "postgres",
            "postgres_dsn": "postgresql://job_ftch:job_ftch@localhost:5432/job_ftch",
        }
    )

    assert settings.store_backend == "postgres"
    assert settings.postgres_dsn == "postgresql://job_ftch:job_ftch@localhost:5432/job_ftch"
