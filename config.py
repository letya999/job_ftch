"""Application configuration via pydantic-settings."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_VALID_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


class SourceBackend(StrEnum):
    LOCAL_FIXTURE = "local_fixture"


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


settings = Settings()
