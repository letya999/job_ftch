"""Application configuration via pydantic-settings."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_VALID_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


class Settings(BaseSettings):
    """Pipeline settings loaded from environment variables."""

    source_backend: str = "local_fixture"
    sink_backend: str = "json_file"
    store_backend: str = "postgres"  # "memory" (tests), "sqlite", "postgres" (default)
    job_group_store_backend: str = "sqlite"
    llm_backend: str = "heuristic"
    posting_backend: str = "none"
    notify_mode: str = "instant"  # "instant" (per job) or "digest" (once per run)
    notify_batch_size: int = 10
    log_level: str = "INFO"
    telemetry_service_name: str = "job_ftch"
    telemetry_console_exporter: bool = False
    metrics_enabled: bool = False
    metrics_port: int = Field(default=9090, gt=0, le=65535)
    pipeline_max_items_per_run: int = Field(default=200, gt=0)
    pipeline_max_text_length: int = Field(default=20_000, ge=256, le=500_000)
    schedule_interval_seconds: int | None = None
    configs_dir: Path | None = None
    dry_run: bool = False
    tenant_id: str | None = None
    tenant_display_name: str | None = None
    auth_file_path: Path | None = None
    sources_file_path: Path | None = None
    filter_profile_path: Path | None = None
    debug_source_path: Path = Path("fixtures/debug/raw_items.json")
    output_path: Path = Path("artifacts/debug/raw_items.json")
    output_jsonl: bool = False
    output_schema_version: str | None = "job_ftch.job.v1"
    quarantine_output_path: Path = Path("artifacts/debug/quarantine.jsonl")
    quarantine_output_jsonl: bool = True
    quarantine_output_schema_version: str | None = "job_ftch.quarantine.v1"
    review_output_path: Path = Path("artifacts/debug/review.jsonl")
    review_output_jsonl: bool = True
    review_output_schema_version: str | None = "job_ftch.job.v1"
    rejected_output_path: Path = Path("artifacts/debug/rejected.jsonl")
    rejected_output_jsonl: bool = True
    rejected_output_schema_version: str | None = "job_ftch.rejected.v1"
    review_max_quality_score: float = Field(default=0.65, ge=0.0, le=1.0)
    posting_min_quality_score: float = Field(default=0.8, ge=0.0, le=1.0)
    routing_accept_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    bot_send_limit_per_run: int = Field(default=15, ge=1, le=50)
    bot_min_quality_score: float = Field(default=0.0, ge=0.0, le=1.0)
    bot_min_relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    extraction_min_search_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    pipeline_max_llm_calls_per_run: int | None = Field(default=None)
    routing_review_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    routing_quality_override_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_session_path: Path = Path(".runtime/telegram.session")
    telegram_entity: str | None = None
    telegram_publish_entity: str | None = None
    telegram_message_limit: int = Field(default=100, gt=0)
    telegram_comment_post_limit: int = Field(default=20, gt=0)
    telegram_comment_limit_per_post: int = Field(default=50, gt=0)
    telegram_jitter_min_seconds: float = Field(default=1.0, ge=0.0, le=60.0)
    telegram_jitter_max_seconds: float = Field(default=4.0, ge=0.0, le=120.0)
    telegram_proxy_type: str | None = None
    telegram_proxy_host: str | None = None
    telegram_proxy_port: int | None = None
    telegram_proxy_username: str | None = None
    telegram_proxy_password: str | None = None
    telegram_timeout_seconds: float = Field(default=10.0, gt=0.0, le=120.0)
    telegram_request_retries: int = Field(default=3, ge=0, le=20)
    telegram_connection_retries: int = Field(default=3, ge=0, le=20)
    telegram_retry_delay_seconds: float = Field(default=1.0, ge=0.0, le=60.0)
    telegram_flood_sleep_threshold_seconds: int = Field(default=60, ge=0, le=86400)
    telegram_history_wait_time_seconds: float = Field(default=0.0, ge=0.0)
    telegram_channel_default_limit: int = Field(default=50, gt=0)
    telegram_group_default_limit: int = Field(default=50, gt=0)
    telegram_comment_default_limit: int = Field(default=20, gt=0)
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.4-nano"
    openai_base_url: str | None = None
    openai_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    openai_max_retries: int = Field(default=2, ge=0, le=10)
    career_site_url: str | None = None
    career_site_default_limit: int = Field(default=50, gt=0)
    career_site_default_detail_limit: int | None = Field(default=None, ge=1)
    career_site_timeout_seconds: float = Field(default=15.0, gt=0.0, le=300.0)
    career_site_connect_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    career_site_max_retries: int = Field(default=2, ge=0, le=10)
    career_site_retry_delay_seconds: float = Field(default=1.0, ge=0.0, le=60.0)
    career_site_max_connections: int = Field(default=10, gt=0, le=200)
    career_site_max_keepalive_connections: int = Field(default=5, gt=0, le=200)
    career_site_detail_concurrency: int = Field(default=5, gt=0, le=50)
    browser_default_timeout_ms: int = Field(default=30000, gt=0)
    browser_context_timeout_ms: int = Field(default=120000, gt=0)
    browser_challenge_retries: int = Field(default=1, ge=0)
    monitor_timeout_seconds: float = Field(default=15.0, gt=0.0, le=300.0)
    monitor_max_retries: int = Field(default=3, ge=0, le=10)
    rss_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    api_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    ollama_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    fingerprinter_timeout_seconds: float = Field(default=8.0, gt=0.0, le=300.0)
    store_path: Path = Path(".runtime/job_ftch.db")
    store_dsn: str | None = None
    store_pool_min: int = Field(default=2, gt=0)
    store_pool_max: int = Field(default=10, gt=0)
    store_fallback_on_error: bool = True
    memory_max_keys: int = Field(default=50_000, gt=0)
    memory_max_set_members: int = Field(default=50_000, gt=0)
    source_health_drift_ratio: float = Field(default=0.2, ge=0.0, le=1.0)
    source_health_min_baseline: float = Field(default=3.0, ge=0.0)
    source_health_failure_streak_pause: int = Field(default=3, gt=0)
    source_health_probe_every_n_runs: int = Field(default=5, gt=0)
    scheduler_jitter_seconds: float = Field(default=0.0, ge=0.0)
    job_backend: str = "sqlite"
    search_backend: str = "sqlite"
    job_store_path: Path | None = None
    search_language: str = "simple"
    embedding_enabled: bool = False
    embedding_prefilter_enabled: bool = False
    embedding_prefilter_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    extraction_min_hiring_intent: float = Field(default=0.0, ge=0.0, le=1.0)
    language_detection_enabled: bool = False
    translation_enabled: bool = False
    translation_target_language: str = "ru"
    reranker_enabled: bool = False
    reranker_model: str = "jina-v2-multilingual"
    reranker_top_k: int = 50
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int | None = None
    vector_backend: str | None = None
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    qdrant_collection: str = "job_ftch_jobs"
    ollama_base_url: str = "http://localhost:11434"

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

    @field_validator(
        "source_backend",
        "sink_backend",
        "store_backend",
        "job_group_store_backend",
        "llm_backend",
        "posting_backend",
        "job_backend",
        "search_backend",
        "embedding_provider",
    )
    @classmethod
    def normalize_backend_keys(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            msg = "backend keys must not be blank"
            raise ValueError(msg)
        return normalized

    @model_validator(mode="after")
    def validate_postgres_dsn(self) -> Settings:
        if self.store_backend == "postgres" and not self.store_dsn:
            raise ValueError(
                "store_backend='postgres' requires STORE_DSN env var to be set. "
                "Example: STORE_DSN='postgresql+asyncpg://user:pass@host:5432/dbname'. "
                "Set store_backend='sqlite' or 'memory' to run without Postgres."
            )
        return self

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
        "openai_api_key",
        "openai_model",
        "openai_base_url",
        "telegram_publish_entity",
        "output_schema_version",
        "quarantine_output_schema_version",
        "review_output_schema_version",
        "rejected_output_schema_version",
        "store_dsn",
        "vector_backend",
        "qdrant_url",
        "qdrant_api_key",
        "qdrant_collection",
        "ollama_base_url",
        "embedding_model",
        "search_language",
        "reranker_model",
        "translation_target_language",
        "tenant_id",
        "tenant_display_name",
        "telegram_proxy_type",
        "telegram_proxy_host",
        "telegram_proxy_username",
        "telegram_proxy_password",
        "career_site_url",
    )
    @classmethod
    def strip_optional_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def validate_telegram_jitter(self) -> Settings:
        if self.telegram_jitter_min_seconds > self.telegram_jitter_max_seconds:
            raise ValueError(
                "telegram_jitter_min_seconds cannot be greater than telegram_jitter_max_seconds."
            )
        return self

    @model_validator(mode="after")
    def validate_dependencies(self) -> Settings:
        if self.llm_backend == "openai":
            if self.openai_api_key is None:
                msg = "openai_api_key is required when llm_backend=openai."
                raise ValueError(msg)
            if self.openai_model is None:
                msg = "openai_model is required when llm_backend=openai."
                raise ValueError(msg)
        if self.posting_backend == "telegram_posting":
            if self.telegram_publish_entity is None:
                msg = "telegram_publish_entity is required when posting_backend=telegram_posting."
                raise ValueError(msg)
            if self.telegram_api_id is None or self.telegram_api_hash is None:
                msg = "Telegram posting requires JOB_FTCH_TELEGRAM_API_ID and JOB_FTCH_TELEGRAM_API_HASH."
                raise ValueError(msg)

        if self.job_backend == "postgres" and not self.store_dsn:
            msg = "store_dsn is required when job_backend=postgres."
            raise ValueError(msg)
        if self.search_backend == "postgres" and not self.store_dsn:
            msg = "store_dsn is required when search_backend=postgres."
            raise ValueError(msg)
        if self.vector_backend == "pgvector" and not self.store_dsn:
            msg = "store_dsn is required when vector_backend=pgvector."
            raise ValueError(msg)
        if self.vector_backend == "qdrant" and not self.qdrant_url:
            msg = "qdrant_url is required when vector_backend=qdrant."
            raise ValueError(msg)
        if self.embedding_enabled and not self.vector_backend:
            msg = "vector_backend is required when embedding_enabled=True."
            raise ValueError(msg)
        if (
            self.embedding_enabled
            and self.embedding_provider == "openai"
            and not self.openai_api_key
        ):
            msg = "openai_api_key is required when embedding_provider=openai."
            raise ValueError(msg)

        return self

    def _variant_settings(
        self,
        *,
        output_path: Path,
        output_jsonl: bool,
        output_schema_version: str | None,
        sink_backend: str | None = None,
    ) -> Settings:
        payload = self.model_dump(mode="python")
        payload["output_path"] = output_path
        payload["output_jsonl"] = output_jsonl
        payload["output_schema_version"] = output_schema_version
        if sink_backend is not None:
            payload["sink_backend"] = sink_backend
        return self.__class__.model_validate(payload)

    def quarantine_settings(self) -> Settings:
        return self._variant_settings(
            output_path=self.quarantine_output_path,
            output_jsonl=self.quarantine_output_jsonl,
            output_schema_version=self.quarantine_output_schema_version,
        )

    def review_settings(self) -> Settings:
        return self._variant_settings(
            output_path=self.review_output_path,
            output_jsonl=self.review_output_jsonl,
            output_schema_version=self.review_output_schema_version,
        )

    def rejected_settings(self) -> Settings:
        return self._variant_settings(
            output_path=self.rejected_output_path,
            output_jsonl=self.rejected_output_jsonl,
            output_schema_version=self.rejected_output_schema_version,
        )

    def posting_settings(self) -> Settings:
        return self._variant_settings(
            output_path=self.output_path,
            output_jsonl=self.output_jsonl,
            output_schema_version=self.output_schema_version,
            sink_backend=self.posting_backend,
        )


def get_settings() -> Settings:
    return Settings()
