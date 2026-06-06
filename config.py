"""Application configuration via pydantic-settings."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_VALID_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
StringTuple = Annotated[tuple[str, ...], NoDecode]


class SourceBackend(StrEnum):
    LOCAL_FIXTURE = "local_fixture"
    TELEGRAM_CHANNEL = "telegram_channel"
    TELEGRAM_GROUP = "telegram_group"
    TELEGRAM_COMMENT = "telegram_comment"
    CAREER_SITE = "career_site"


class SinkBackend(StrEnum):
    JSON_FILE = "json_file"


class StoreBackend(StrEnum):
    MEMORY = "memory"
    POSTGRES = "postgres"


class LLMBackend(StrEnum):
    DISABLED = "disabled"
    OPENAI = "openai"


class Settings(BaseSettings):
    """Pipeline settings loaded from environment variables."""

    source_backend: SourceBackend = SourceBackend.LOCAL_FIXTURE
    sink_backend: SinkBackend = SinkBackend.JSON_FILE
    store_backend: StoreBackend = StoreBackend.MEMORY
    log_level: str = "INFO"
    telemetry_service_name: str = "job_ftch"
    telemetry_console_exporter: bool = False
    pipeline_max_items_per_run: int = Field(default=200, gt=0)
    pipeline_max_source_errors: int = Field(default=20, ge=0)
    dry_run: bool = False
    max_text_length: int = Field(default=20_000, gt=0)
    dedup_threshold: int = Field(default=90, ge=0, le=100)
    debug_source_path: Path = Path("fixtures/debug/raw_items.json")
    output_path: Path = Path("artifacts/debug/raw_items.json")
    output_jsonl: bool = False
    quarantine_output_path: Path = Path("artifacts/debug/quarantine.jsonl")
    quarantine_output_jsonl: bool = True
    jobs_output_path: Path = Path("artifacts/debug/jobs.jsonl")
    jobs_output_jsonl: bool = True
    rejected_output_path: Path = Path("artifacts/debug/rejected_items.jsonl")
    rejected_output_jsonl: bool = True
    review_output_path: Path = Path("artifacts/debug/review_items.jsonl")
    review_output_jsonl: bool = True
    run_summary_output_path: Path = Path("artifacts/debug/run_summary.json")
    postgres_dsn: str | None = None
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_session_path: Path = Path(".runtime/telegram.session")
    telegram_entity: str | None = None
    telegram_entities: StringTuple = ()
    telegram_message_limit: int = Field(default=100, gt=0)
    telegram_comment_post_limit: int = Field(default=20, gt=0)
    telegram_comment_limit_per_post: int = Field(default=50, gt=0)
    telegram_history_wait_time_seconds: float = Field(default=1.0, ge=0.0, le=60.0)
    telegram_flood_sleep_threshold_seconds: int = Field(default=60, ge=0, le=86400)
    http_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    http_max_retries: int = Field(default=2, ge=0, le=10)
    http_max_pages_per_source: int = Field(default=50, gt=0)
    career_site_url: str | None = None
    career_site_urls: StringTuple = ()
    career_site_config_path: Path | None = None
    career_site_allowed_hosts: StringTuple = (
        "job-boards.greenhouse.io",
        "www.bcc.kz",
        "bcc.kz",
    )
    llm_backend: LLMBackend = LLMBackend.DISABLED
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    llm_max_retries: int = Field(default=2, ge=0, le=10)
    llm_max_calls_per_run: int = Field(default=0, ge=0)
    extraction_main_quality_threshold: float = Field(default=0.70, ge=0.0, le=1.0)
    extraction_review_quality_threshold: float = Field(default=0.40, ge=0.0, le=1.0)

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.dev"),
        env_prefix="JOB_FTCH_",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in _VALID_LOG_LEVELS:
            msg = f"log_level must be one of: {', '.join(sorted(_VALID_LOG_LEVELS))}"
            raise ValueError(msg)
        return normalized

    @field_validator("telegram_api_id", mode="before")
    @classmethod
    def normalize_optional_int(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "telegram_api_hash",
        "telegram_entity",
        "career_site_url",
        "postgres_dsn",
        "llm_model",
        "llm_base_url",
        "llm_api_key",
    )
    @classmethod
    def strip_optional_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("career_site_config_path", mode="before")
    @classmethod
    def normalize_optional_path(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("telegram_entities", "career_site_urls", mode="before")
    @classmethod
    def normalize_string_tuple(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",")]
            return tuple(part for part in parts if part)
        if isinstance(value, (list, tuple)):
            return tuple(str(part).strip() for part in value if str(part).strip())
        return value

    @field_validator("career_site_allowed_hosts", mode="before")
    @classmethod
    def normalize_career_site_allowed_hosts(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, str):
            parts = [part.strip().lower() for part in value.split(",")]
            return tuple(part for part in parts if part)
        if isinstance(value, (list, tuple)):
            return tuple(str(part).strip().lower() for part in value if str(part).strip())
        return value

    @model_validator(mode="after")
    def validate_career_site_policy(self) -> Settings:
        if self.source_backend is not SourceBackend.CAREER_SITE or self.career_site_url is None:
            return self

        parsed = urlsplit(self.career_site_url)
        if parsed.scheme != "https":
            msg = "career_site_url must use https."
            raise ValueError(msg)

        host = parsed.hostname.lower() if parsed.hostname is not None else None
        if host is None or host not in self.career_site_allowed_hosts:
            allowed = ", ".join(self.career_site_allowed_hosts)
            msg = f"career_site_url host must be one of: {allowed}"
            raise ValueError(msg)

        return self

    @model_validator(mode="after")
    def validate_llm_policy(self) -> Settings:
        if self.llm_backend is LLMBackend.DISABLED:
            return self
        if self.llm_backend is LLMBackend.OPENAI:
            missing: list[str] = []
            if self.llm_api_key is None:
                missing.append("JOB_FTCH_LLM_API_KEY")
            if self.llm_model is None:
                missing.append("JOB_FTCH_LLM_MODEL")
            if missing:
                msg = f"OpenAI LLM backend requires: {', '.join(missing)}"
                raise ValueError(msg)
            return self
        msg = f"Unsupported llm_backend: {self.llm_backend}"
        raise ValueError(msg)

    @model_validator(mode="after")
    def validate_store_policy(self) -> Settings:
        if self.store_backend is StoreBackend.POSTGRES and self.postgres_dsn is None:
            msg = "PostgreSQL store backend requires JOB_FTCH_POSTGRES_DSN."
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def validate_quality_thresholds(self) -> Settings:
        if self.extraction_review_quality_threshold > self.extraction_main_quality_threshold:
            msg = (
                "extraction_review_quality_threshold must be less than or equal to "
                "extraction_main_quality_threshold."
            )
            raise ValueError(msg)
        return self


settings = Settings()
