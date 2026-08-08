from __future__ import annotations

import pytest

from job_ftch.config import Settings


def test_settings_allow_blank_optional_telegram_credentials_for_non_telegram_backends() -> None:
    settings = Settings.model_validate(
        {
            "source_backend": "career_site",
            "llm_backend": "heuristic",
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
            "llm_backend": "heuristic",
            "output_path": "artifacts/debug/raw_items.json",
            "quarantine_output_path": "artifacts/debug/quarantine.jsonl",
            "quarantine_output_jsonl": True,
        }
    )

    quarantine = settings.quarantine_settings()

    assert quarantine.output_path == settings.quarantine_output_path
    assert quarantine.output_jsonl is True


def test_openai_backend_settings_can_load_without_api_key_for_non_llm_paths() -> None:
    settings = Settings.model_validate({"llm_backend": "openai", "openai_api_key": None})

    assert settings.llm_backend == "openai"
    assert settings.openai_api_key is None


def test_openai_provider_requires_api_key() -> None:
    from job_ftch.infrastructure.llm.openai_provider import _build_openai_llm

    settings = Settings.model_validate({"llm_backend": "openai", "openai_api_key": None})

    with pytest.raises(ValueError, match="openai_api_key is required"):
        _build_openai_llm(settings)


def test_pytest_default_settings_keep_openai_backend_without_private_env() -> None:
    from job_ftch.config import get_settings

    settings = get_settings()

    assert settings.llm_backend == "openai"
    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "sk-test-offline-pytest-openai-key"


def test_settings_accept_standard_openai_api_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOB_FTCH_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-standard-openai-key")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.llm_backend == "openai"
    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "sk-test-standard-openai-key"


def test_job_ftch_openai_api_key_env_takes_project_scoped_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JOB_FTCH_OPENAI_API_KEY", "sk-test-project-openai-key")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-standard-openai-key")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "sk-test-project-openai-key"


def test_review_and_rejected_settings_switch_output_targets() -> None:
    settings = Settings.model_validate(
        {
            "llm_backend": "heuristic",
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


def test_postgres_backend_without_dsn_is_warned_not_raised() -> None:
    """Per ADR-034: store_backend='postgres' without DSN no longer raises.

    It logs a warning and lets the auto-resolver pick memory/sqlite. Production
    deployments that need postgres should set both JOB_FTCH_STORE_BACKEND=postgres
    and JOB_FTCH_STORE_DSN explicitly.
    """
    settings = Settings.model_validate(
        {
            "llm_backend": "heuristic",
            "store_backend": "postgres",
            "store_dsn": None,
            "job_backend": "sqlite",
        }
    )
    assert settings.store_backend == "postgres"
    assert settings.store_dsn is None


def test_postgres_backend_valid_dsn() -> None:
    settings = Settings.model_validate(
        {
            "llm_backend": "heuristic",
            "store_backend": "postgres",
            "store_dsn": "postgresql+asyncpg://user:pass@host/db",
        }
    )
    assert settings.store_backend == "postgres"
    assert settings.store_dsn is not None
    assert settings.store_dsn.get_secret_value() == "postgresql+asyncpg://user:pass@host/db"


def test_store_backend_auto_default() -> None:
    """Per ADR-034: the default for store_backend is 'auto'."""
    # Check the class-level default rather than the resolved value, because
    # Settings reads from .env / .env.dev at construction time.
    from job_ftch.config import Settings

    assert Settings.model_fields["store_backend"].default == "auto"


def test_resolve_store_backend_auto_picks_sqlite_when_no_dsn() -> None:
    """resolve_store_backend('auto') falls back to sqlite on a writable path."""
    from job_ftch.application.registry import resolve_store_backend

    settings = Settings.model_validate({"llm_backend": "heuristic"})
    resolved = resolve_store_backend(settings)
    assert resolved in {"sqlite", "memory"}


def test_resolve_store_backend_auto_picks_postgres_when_dsn_set() -> None:
    from job_ftch.application.registry import resolve_store_backend

    settings = Settings.model_validate(
        {
            "llm_backend": "heuristic",
            "store_backend": "auto",
            "store_dsn": "postgresql+asyncpg://u:p@h/db",
        }
    )
    assert resolve_store_backend(settings) == "postgres"


def test_store_dsn_not_in_settings_repr() -> None:
    dsn = "postgresql://user:secret@host/db"
    settings = Settings.model_validate({"llm_backend": "heuristic", "store_dsn": dsn})
    rendered = repr(settings)
    assert dsn not in rendered
    assert "postgresql://user:" not in rendered


def test_resolve_store_backend_passes_through_explicit() -> None:
    from job_ftch.application.registry import resolve_store_backend

    settings = Settings.model_validate({"llm_backend": "heuristic", "store_backend": "memory"})
    assert resolve_store_backend(settings) == "memory"


def test_relevance_window_rejects_dead_band() -> None:
    with pytest.raises(ValueError, match="dead band"):
        Settings.model_validate(
            {
                "llm_backend": "heuristic",
                "routing_accept_threshold": 0.55,
                "llm_relevance_high_threshold": 0.54,
            }
        )


def test_resolve_env_files_defaults_to_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    from job_ftch.config import _resolve_env_files

    monkeypatch.delenv("JOB_FTCH_ENV", raising=False)
    assert _resolve_env_files() == (".env", ".env.dev")


def test_resolve_env_files_prod_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    from job_ftch.config import _resolve_env_files

    monkeypatch.setenv("JOB_FTCH_ENV", "PROD")
    assert _resolve_env_files() == (".env", ".env.prod")
    monkeypatch.setenv("JOB_FTCH_ENV", "production")
    assert _resolve_env_files() == (".env", ".env.prod")


def test_resolve_runtime_config_files_defaults_to_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    from job_ftch.config import _resolve_runtime_config_files

    monkeypatch.delenv("JOB_FTCH_ENV", raising=False)
    monkeypatch.delenv("JOB_FTCH_RUNTIME_CONFIG_PATH", raising=False)
    assert _resolve_runtime_config_files() == ("config/runtime.yaml", "config/runtime.dev.yaml")


def test_resolve_runtime_config_files_prod_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    from job_ftch.config import _resolve_runtime_config_files

    monkeypatch.setenv("JOB_FTCH_ENV", "production")
    monkeypatch.delenv("JOB_FTCH_RUNTIME_CONFIG_PATH", raising=False)
    assert _resolve_runtime_config_files() == ("config/runtime.yaml", "config/runtime.prod.yaml")


def test_resolve_runtime_config_files_explicit_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from job_ftch.config import _resolve_runtime_config_files

    monkeypatch.setenv(
        "JOB_FTCH_RUNTIME_CONFIG_PATH", "config/runtime.yaml;config/runtime.dev.yaml"
    )
    assert _resolve_runtime_config_files() == ("config/runtime.yaml", "config/runtime.dev.yaml")


def test_resolve_runtime_config_files_explicit_missing_path_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.config import _resolve_runtime_config_files

    monkeypatch.setenv("JOB_FTCH_RUNTIME_CONFIG_PATH", "config/runtime.yaml;missing.yaml")

    with pytest.raises(FileNotFoundError, match="JOB_FTCH_RUNTIME_CONFIG_PATH.*missing.yaml"):
        _resolve_runtime_config_files()


def test_resolve_runtime_config_files_accepts_windows_separator_in_linux_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.config import _resolve_runtime_config_files

    monkeypatch.setattr("job_ftch.config.os.pathsep", ":")
    monkeypatch.setenv(
        "JOB_FTCH_RUNTIME_CONFIG_PATH",
        "config/runtime.yaml;config/runtime.dev.yaml;job_ftch/adapters/telegram_bot/runtime.dev.yaml",
    )

    assert _resolve_runtime_config_files() == (
        "config/runtime.yaml",
        "config/runtime.dev.yaml",
        "job_ftch/adapters/telegram_bot/runtime.dev.yaml",
    )


def test_runtime_yaml_applies_as_baseline(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_yaml = tmp_path / "runtime.yaml"
    runtime_yaml.write_text(
        "routing_accept_threshold: 0.77\nllm_relevance_high_threshold: 0.99\npipeline_item_concurrency: 9\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JOB_FTCH_RUNTIME_CONFIG_PATH", str(runtime_yaml))
    monkeypatch.delenv("JOB_FTCH_ROUTING_ACCEPT_THRESHOLD", raising=False)
    monkeypatch.delenv("JOB_FTCH_PIPELINE_ITEM_CONCURRENCY", raising=False)

    settings = Settings(_env_file=None, llm_backend="heuristic")  # type: ignore[call-arg]

    assert settings.routing_accept_threshold == pytest.approx(0.77)
    assert settings.pipeline_item_concurrency == 9


def test_env_overrides_runtime_yaml(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_yaml = tmp_path / "runtime.yaml"
    runtime_yaml.write_text(
        "routing_accept_threshold: 0.77\nllm_relevance_high_threshold: 0.99\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JOB_FTCH_RUNTIME_CONFIG_PATH", str(runtime_yaml))
    monkeypatch.setenv("JOB_FTCH_ROUTING_ACCEPT_THRESHOLD", "0.66")
    monkeypatch.setenv("JOB_FTCH_LLM_RELEVANCE_HIGH_THRESHOLD", "0.99")

    settings = Settings(_env_file=None, llm_backend="heuristic")  # type: ignore[call-arg]

    assert settings.routing_accept_threshold == pytest.approx(0.66)


def test_init_kwargs_override_runtime_yaml(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_yaml = tmp_path / "runtime.yaml"
    runtime_yaml.write_text(
        "routing_accept_threshold: 0.77\nllm_relevance_high_threshold: 0.99\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JOB_FTCH_RUNTIME_CONFIG_PATH", str(runtime_yaml))
    monkeypatch.delenv("JOB_FTCH_ROUTING_ACCEPT_THRESHOLD", raising=False)
    monkeypatch.delenv("JOB_FTCH_LLM_RELEVANCE_HIGH_THRESHOLD", raising=False)

    settings = Settings(_env_file=None, llm_backend="heuristic", routing_accept_threshold=0.61)  # type: ignore[call-arg]

    assert settings.routing_accept_threshold == pytest.approx(0.61)
