"""Application configuration via pydantic-settings."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_VALID_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


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


class Settings(BaseSettings):
    """Pipeline settings loaded from environment variables."""

    source_backend: SourceBackend = SourceBackend.LOCAL_FIXTURE
    sink_backend: SinkBackend = SinkBackend.JSON_FILE
    store_backend: StoreBackend = StoreBackend.MEMORY
    log_level: str = "INFO"
    telemetry_service_name: str = "job_ftch"
    telemetry_console_exporter: bool = False
    pipeline_max_items_per_run: int = Field(default=200, gt=0)
    debug_source_path: Path = Path("fixtures/debug/raw_items.json")
    output_path: Path = Path("artifacts/debug/raw_items.json")
    output_jsonl: bool = False
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_session_path: Path = Path(".runtime/telegram.session")
    telegram_entity: str | None = None
    telegram_message_limit: int = Field(default=100, gt=0)
    telegram_comment_post_limit: int = Field(default=20, gt=0)
    telegram_comment_limit_per_post: int = Field(default=50, gt=0)
    telegram_history_wait_time_seconds: float = Field(default=1.0, ge=0.0, le=60.0)
    telegram_flood_sleep_threshold_seconds: int = Field(default=60, ge=0, le=86400)
    career_site_url: str | None = None
    career_site_allowed_hosts: tuple[str, ...] = (
        "job-boards.greenhouse.io",
        "www.bcc.kz",
        "bcc.kz",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
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

    @field_validator("telegram_api_hash", "telegram_entity", "career_site_url")
    @classmethod
    def strip_optional_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

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


settings = Settings()
