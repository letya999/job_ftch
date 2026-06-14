from __future__ import annotations

import pytest

from job_ftch.config import Settings


def test_settings_allow_blank_optional_telegram_credentials_for_non_telegram_backends() -> None:
    settings = Settings.model_validate(
        {
            "source_backend": "career_site",
            "telegram_api_id": "",
            "telegram_api_hash": "",
            "telegram_entity": "",
        }
    )

    assert settings.source_backend == "career_site"
    assert settings.telegram_api_id is None
    assert settings.telegram_api_hash is None
    assert settings.telegram_entity is None





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


def test_openai_backend_requires_api_key() -> None:
    with pytest.raises(ValueError, match="openai_api_key is required"):
        Settings.model_validate({"llm_backend": "openai", "openai_api_key": None})


def test_review_and_rejected_settings_switch_output_targets() -> None:
    settings = Settings.model_validate(
        {
            "review_output_path": "artifacts/debug/review.jsonl",
            "review_output_jsonl": True,
            "rejected_output_path": "artifacts/debug/rejected.jsonl",
            "rejected_output_jsonl": True,
        }
    )

    review = settings.review_settings()
    rejected = settings.rejected_settings()

    assert review.output_path == settings.review_output_path
    assert review.output_jsonl is True
    assert rejected.output_path == settings.rejected_output_path
    assert rejected.output_jsonl is True





def test_postgres_backend_requires_dsn() -> None:
    with pytest.raises(ValueError, match="store_backend='postgres' requires STORE_DSN"):
        Settings.model_validate({"store_backend": "postgres", "store_dsn": None})


def test_postgres_backend_valid_dsn() -> None:
    settings = Settings.model_validate(
        {"store_backend": "postgres", "store_dsn": "postgresql+asyncpg://user:pass@host/db"}
    )
    assert settings.store_backend == "postgres"
    assert settings.store_dsn == "postgresql+asyncpg://user:pass@host/db"
