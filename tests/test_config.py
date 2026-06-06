from __future__ import annotations

from pathlib import Path

import pytest

from config import LLMBackend, Settings, SourceBackend, StoreBackend


def test_settings_defaults_include_mvp_output_contracts() -> None:
    settings = Settings.model_validate({})

    assert settings.dry_run is False
    assert settings.max_text_length == 20_000
    assert settings.dedup_threshold == 90
    assert settings.jobs_output_path.as_posix() == "artifacts/debug/jobs.jsonl"
    assert settings.rejected_output_path.as_posix() == "artifacts/debug/rejected_items.jsonl"
    assert settings.review_output_path.as_posix() == "artifacts/debug/review_items.jsonl"
    assert settings.run_summary_output_path.as_posix() == "artifacts/debug/run_summary.json"
    assert settings.postgres_dsn is None
    assert settings.store_backend is StoreBackend.MEMORY
    assert settings.career_site_config_path is None
    assert settings.llm_backend is LLMBackend.DISABLED


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


def test_settings_load_prefixed_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOB_FTCH_DRY_RUN", "true")
    monkeypatch.setenv("JOB_FTCH_MAX_TEXT_LENGTH", "1234")
    monkeypatch.setenv("JOB_FTCH_DEDUP_THRESHOLD", "77")
    monkeypatch.setenv("JOB_FTCH_REJECTED_OUTPUT_PATH", "artifacts/test/rejected.jsonl")
    monkeypatch.setenv("JOB_FTCH_HTTP_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("JOB_FTCH_HTTP_MAX_RETRIES", "3")
    monkeypatch.setenv("JOB_FTCH_STORE_BACKEND", "postgres")
    monkeypatch.setenv(
        "JOB_FTCH_POSTGRES_DSN",
        "postgresql://job_ftch:job_ftch@localhost:5432/job_ftch",
    )
    monkeypatch.setenv("JOB_FTCH_TELEGRAM_ENTITIES", "ai_jobs, ml_jobs")
    monkeypatch.setenv(
        "JOB_FTCH_CAREER_SITE_URLS",
        "https://job-boards.greenhouse.io/example, https://www.bcc.kz/jobs",
    )

    settings = Settings(_env_file=None)

    assert settings.dry_run is True
    assert settings.max_text_length == 1234
    assert settings.dedup_threshold == 77
    assert settings.rejected_output_path.as_posix() == "artifacts/test/rejected.jsonl"
    assert settings.http_timeout_seconds == 12.5
    assert settings.http_max_retries == 3
    assert settings.store_backend is StoreBackend.POSTGRES
    assert settings.postgres_dsn == "postgresql://job_ftch:job_ftch@localhost:5432/job_ftch"
    assert settings.telegram_entities == ("ai_jobs", "ml_jobs")
    assert settings.career_site_urls == (
        "https://job-boards.greenhouse.io/example",
        "https://www.bcc.kz/jobs",
    )


def test_openai_llm_backend_requires_model_and_api_key() -> None:
    with pytest.raises(ValueError, match="OpenAI LLM backend requires"):
        Settings.model_validate({"llm_backend": LLMBackend.OPENAI})


def test_openai_llm_backend_allows_explicit_credentials() -> None:
    settings = Settings.model_validate(
        {
            "llm_backend": LLMBackend.OPENAI,
            "llm_model": "configured-model",
            "llm_api_key": "test-key",
        }
    )

    assert settings.llm_backend is LLMBackend.OPENAI
    assert settings.llm_model == "configured-model"
    assert settings.llm_api_key == "test-key"


def test_postgres_store_backend_requires_dsn() -> None:
    with pytest.raises(ValueError, match="PostgreSQL store backend requires"):
        Settings.model_validate({"store_backend": StoreBackend.POSTGRES})


def test_postgres_store_backend_allows_explicit_dsn() -> None:
    settings = Settings.model_validate(
        {
            "store_backend": StoreBackend.POSTGRES,
            "postgres_dsn": "postgresql://job_ftch:job_ftch@localhost:5432/job_ftch",
        }
    )

    assert settings.store_backend is StoreBackend.POSTGRES
    assert settings.postgres_dsn == "postgresql://job_ftch:job_ftch@localhost:5432/job_ftch"


def test_settings_reject_invalid_quality_threshold_order() -> None:
    with pytest.raises(
        ValueError,
        match="extraction_review_quality_threshold must be less than or equal",
    ):
        Settings.model_validate(
            {
                "extraction_main_quality_threshold": 0.4,
                "extraction_review_quality_threshold": 0.7,
            }
        )


def test_env_examples_only_use_known_prefixed_settings() -> None:
    field_names = set(Settings.model_fields)
    for path in (
        Path(".env.example"),
        Path(".env.dev.example"),
        Path(".env.prod.example"),
    ):
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            key = line.split("=", 1)[0]
            assert key.startswith("JOB_FTCH_"), f"{path}: unprefixed key {key}"
            field_name = key.removeprefix("JOB_FTCH_").lower()
            assert field_name in field_names, f"{path}: unknown settings key {key}"
