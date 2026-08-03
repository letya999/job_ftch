"""Application configuration via pydantic-settings."""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from pydantic_settings.sources import (
    PydanticBaseSettingsSource,
    YamlConfigSettingsSource,
)

_VALID_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


def resolve_site_parsers_manifest_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    configured = os.environ.get("JOB_FTCH_SITE_PARSERS_MANIFEST_PATH", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent / "config" / "site_parsers.yaml"


def _resolve_env_files() -> tuple[str, ...]:
    """Pick dotenv files by JOB_FTCH_ENV, matching the repo convention.

    The repo ships paired environments (.env.dev / .env.prod for the
    pipeline, adapters/<name>/.env.{dev,prod} for adapters) mirrored by
    Dockerfile.{dev,prod} and docker-compose.{dev,prod}.yml. Local
    non-docker runs default to dev; set JOB_FTCH_ENV=prod to switch.
    Real environment variables always override dotenv values, so
    compose-injected env_file entries keep working regardless.
    """
    mode = os.environ.get("JOB_FTCH_ENV", "dev").strip().lower()
    if mode in {"prod", "production"}:
        return (".env", ".env.prod")
    return (".env", ".env.dev")


def _resolve_runtime_config_files() -> tuple[str, ...] | None:
    """Pick the global runtime YAML config file(s).

    Runtime YAML holds non-secret pipeline policy and tuning that should not
    require operator-managed env vars. Real env vars and dotenv files still
    override these values, so runtime YAML acts as a shared baseline rather
    than a stronger source.
    """
    configured = os.environ.get("JOB_FTCH_RUNTIME_CONFIG_PATH", "").strip()
    if configured:
        # Compose files are often authored on Windows (``;``) and executed in
        # Linux containers (``:``).  Accept the explicit Windows separator on
        # every platform before falling back to the native path separator.
        separator = ";" if ";" in configured else os.pathsep
        if separator not in configured and ":" in configured and configured[1:2] != ":":
            separator = ":"
        paths = tuple(part.strip() for part in configured.split(separator) if part.strip())
        missing = [path for path in paths if not Path(path).exists()]
        if missing:
            joined = ", ".join(missing)
            msg = f"Missing runtime YAML path from JOB_FTCH_RUNTIME_CONFIG_PATH: {joined}"
            raise FileNotFoundError(msg)
        return paths or None

    mode = os.environ.get("JOB_FTCH_ENV", "dev").strip().lower()
    if mode in {"prod", "production"}:
        return ("config/runtime.yaml", "config/runtime.prod.yaml")
    return ("config/runtime.yaml", "config/runtime.dev.yaml")


class Settings(BaseSettings):
    """Pipeline settings loaded from environment variables."""

    source_backend: str = "local_fixture"
    sink_backend: str = "json_file"
    store_backend: str = (
        "auto"  # "auto" resolves to memory/sqlite/postgres by availability (ADR-034)
    )
    job_group_store_backend: str = "sqlite"
    llm_backend: str = "openai"
    posting_backend: str = "none"
    notify_mode: str = "instant"  # "instant" (per job) or "digest" (once per run)
    notify_batch_size: int = 10
    log_level: str = "INFO"
    tracing_enabled: bool = False
    tracing_capture_payloads: bool = False
    adaptive_scraping_enabled: bool = Field(
        default=True,
        validation_alias="ADAPTIVE_ENABLED",
    )
    langfuse_host: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    otel_service_name: str = "job_ftch"
    telemetry_service_name: str = "job_ftch"
    telemetry_console_exporter: bool = False
    openobserve_enabled: bool = False
    openobserve_url: str | None = None
    openobserve_org: str = "default"
    openobserve_username: str | None = None
    openobserve_password: SecretStr | None = None
    openobserve_logs_stream: str = "job_ftch_ingest"
    openobserve_timeout_seconds: float = Field(default=5.0, gt=0.0, le=60.0)
    openobserve_metric_export_interval_ms: int = Field(default=10_000, ge=1_000, le=300_000)
    pipeline_max_items_per_run: int | None = Field(default=None, gt=0)
    source_fetch_concurrency: int = Field(default=8, gt=0, le=50)
    source_fetch_concurrency_adaptive: bool = True
    source_preparation_concurrency: int = Field(default=4, gt=0, le=50)
    source_preparation_concurrency_adaptive: bool = True
    pipeline_item_concurrency: int = Field(default=4, gt=0, le=64)
    pipeline_item_concurrency_adaptive: bool = True
    pipeline_max_text_length: int = Field(default=8_000, ge=256, le=500_000)
    schedule_interval_seconds: int | None = None
    configs_dir: Path | None = None
    dry_run: bool = False
    tenant_id: str = "default"
    tenant_display_name: str | None = None
    auth_file_path: Path | None = None
    sources_file_path: Path | None = None
    site_parsers_manifest_path: Path | None = None
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
    review_output_schema_version: str | None = "job_ftch.review.v1"
    derived_ontology_path: str = "fixtures/shots/derived_ontology.json"
    rejected_output_path: Path = Path("artifacts/debug/rejected.jsonl")
    rejected_output_jsonl: bool = True
    rejected_output_schema_version: str | None = "job_ftch.rejected.v1"
    review_max_quality_score: float = Field(default=0.65, ge=0.0, le=1.0)
    posting_min_quality_score: float = Field(default=0.8, ge=0.0, le=1.0)
    routing_accept_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    bot_send_limit_per_run: int = Field(default=100, ge=1, le=100)
    extraction_min_search_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    # TD-017 heuristic extraction fast-path. Default OFF: skipping the LLM
    # extraction for trusted ATS payloads also skips the fields (skills,
    # seniority, role_family) and relevance signals that precision scoring
    # depends on, so it must be validated through the eval harness before being
    # enabled in a precision-tuned deployment.
    pipeline_completeness_gate_enabled: bool = Field(default=True)
    pipeline_completeness_threshold: float = Field(default=0.8)
    pipeline_max_llm_calls_per_run: int | None = Field(default=None)
    pipeline_max_browser_navigations_per_run: int | None = Field(default=None)
    # Full enrichment is a separate post-policy budget. It must not consume
    # the core extraction allowance needed to classify every observation.
    pipeline_full_extraction_max_calls_per_run: int | None = Field(default=100, ge=0)
    routing_review_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    routing_quality_override_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    # Phase 3 cross-encoder gate: when set, bge_reranker_max_score drives routing.
    routing_reranker_accept_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    routing_reranker_review_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    pipeline_decision_version: str = Field(default="pipeline-v1", min_length=1)
    dedup_cache_max_entries: int = Field(default=10_000, ge=100, le=1_000_000)
    evidence_policy_path: Path = Path("config/evidence_policy.yaml")
    # Optional schema-v2 YAML authority. None keeps the schema-v1 compatibility
    # path until tenant-level parity has been verified.
    pipeline_graph_path: Path | None = None
    pipeline_graph_expected_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    snapshot_fail_open: bool = False
    pipeline_candidate_segmentation_enabled: bool = True
    pipeline_jobness_decision_enabled: bool = True
    telegram_api_id: int | None = None
    telegram_api_hash: SecretStr | None = None
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
    telegram_proxy_password: SecretStr | None = None
    telegram_timeout_seconds: float = Field(default=10.0, gt=0.0, le=120.0)
    telegram_request_retries: int = Field(default=3, ge=0, le=20)
    telegram_connection_retries: int = Field(default=3, ge=0, le=20)
    telegram_retry_delay_seconds: float = Field(default=1.0, ge=0.0, le=60.0)
    telegram_flood_sleep_threshold_seconds: int = Field(default=60, ge=0, le=86400)
    telegram_history_wait_time_seconds: float = Field(default=0.0, ge=0.0)
    telegram_channel_default_limit: int = Field(default=50, gt=0)
    telegram_group_default_limit: int = Field(default=50, gt=0)
    telegram_comment_default_limit: int = Field(default=20, gt=0)
    telegram_window_max_messages: int = Field(default=1000, gt=0)
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5.4-nano"
    relevance_llm_model: str = "gpt-4.1-mini"
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
    career_site_detail_concurrency: int = Field(default=8, gt=0, le=50)
    career_site_global_detail_concurrency: int = Field(default=16, gt=0, le=200)
    career_site_browser_concurrency: int = Field(default=3, gt=0, le=32)
    career_site_protection_failure_limit: int = Field(default=3, gt=0, le=20)
    freshness_require_date: bool = False
    browser_channel: str = Field(default="chrome")
    browser_headless: bool = True
    browser_default_timeout_ms: int = Field(default=30000, gt=0)
    browser_context_timeout_ms: int = Field(default=120000, gt=0)
    browser_challenge_retries: int = Field(default=1, ge=0)
    browser_challenge_wait_ms: int = Field(default=6000, gt=0)
    browser_profile_dir: Path | None = None
    browser_profile_persistent: bool = False
    browser_session_state_enabled: bool = False
    browser_session_state_dir: Path = Path(".runtime/session_states")
    api_sniffer_settle_seconds: float = Field(default=4.0, gt=0.0, le=60.0)
    api_sniffer_max_pages: int = Field(default=10, gt=0, le=100)
    api_sniffer_default_page_size: int = Field(default=10, gt=0, le=500)
    api_sniffer_max_responses: int = Field(default=100, gt=0, le=1000)
    api_sniffer_max_response_bytes: int = Field(default=2_000_000, gt=0, le=50_000_000)
    api_sniffer_max_total_bytes: int = Field(default=10_000_000, gt=0, le=200_000_000)
    api_sniffer_decode_concurrency: int = Field(default=4, gt=0, le=32)
    monitor_page_text_max_chars: int = Field(default=500_000, gt=0)
    embedded_state_page_text_max_chars: int = Field(default=1_000_000, gt=0)
    fingerprint_body_scan_max_chars: int = Field(default=100_000, gt=0)
    bypass_timeout_escalate_threshold: int = Field(default=2, ge=1, le=10)
    bypass_max_route_attempts_per_operation: int = Field(default=6, ge=1, le=20)
    bypass_max_same_route_retries_per_operation: int = Field(default=8, ge=0, le=100)
    bypass_max_listing_browser_launches: int = Field(default=3, ge=0, le=10)
    bypass_max_detail_browser_launches: int = Field(default=1, ge=0, le=5)
    bypass_max_source_browser_launches: int = Field(default=16, ge=0, le=100)
    bypass_max_proxy_rotations_per_operation: int = Field(default=2, ge=0, le=10)
    bypass_max_source_proxy_rotations: int = Field(default=8, ge=0, le=50)
    bypass_max_weighted_work_per_source: int = Field(default=500, ge=1, le=10_000)
    bypass_default_requests_per_second: float = Field(default=2.0, ge=0.1, le=20.0)
    # CAPTCHA solving. `captcha_provider` is the external provider used when the
    # free browser-wait tier cannot clear a challenge. It only actually fires if
    # it is also listed in `captcha_enabled_providers`; paid providers
    # (capsolver, capmonster, nextcaptcha, 2captcha, anticaptcha) remain wired
    # in code but are omitted from the default allowlist, so they stay disabled
    # until explicitly enabled.
    captcha_provider: str = Field(default="nopecha")
    captcha_enabled_providers: list[str] = Field(
        default_factory=lambda: ["browser_wait", "nopecha"]
    )
    captcha_provider_routes: dict[str, list[str]] = Field(default_factory=dict)
    # Domains authorized for provider-backed (paid/external) CAPTCHA solving.
    # Empty = none authorized (safe default). The free passive browser_wait tier
    # is never gated by this. Comma-separated env list; parent-suffix match.
    captcha_authorized_domains: Annotated[list[str], NoDecode] = Field(default_factory=list)
    captcha_solver_timeout_budget_seconds: float = Field(default=40.0, ge=0.0, le=180.0)
    captcha_solver_backoff_seconds: float = Field(default=300.0, ge=0.0, le=3600.0)
    proxy_provider: str = Field(default="raw")
    proxy_gateway: str = ""
    proxy_user: str = ""
    proxy_pass: str = ""
    proxy_country_default: str = ""
    proxy_sticky_ttl_seconds: int = Field(default=600, ge=30, le=3600)
    proxy_gb_budget: float = Field(default=0.0, ge=0.0)
    proxy_per_domain_gb_budget: float = Field(default=0.0, ge=0.0)
    proxy_rescue_allow_domains: Annotated[list[str], NoDecode] = Field(default_factory=list)
    proxy_rescue_deny_domains: Annotated[list[str], NoDecode] = Field(default_factory=list)
    proxy_strict_geo: bool = False
    robots_enforce: bool = False
    session_memory_enabled: bool = False
    monitor_timeout_seconds: float = Field(default=15.0, gt=0.0, le=300.0)
    monitor_max_retries: int = Field(default=3, ge=0, le=10)
    rss_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    api_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    ollama_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    fingerprinter_timeout_seconds: float = Field(default=8.0, gt=0.0, le=300.0)
    store_path: Path = Path(".runtime/job_ftch.db")
    store_dsn: SecretStr | None = None
    http_proxy_list: Annotated[list[str], NoDecode] = Field(default_factory=list)
    store_pool_min: int = Field(default=2, gt=0)
    store_pool_max: int = Field(default=10, gt=0)
    store_fallback_on_error: bool = True
    store_allow_fallback: bool = False
    memory_max_keys: int = Field(default=50_000, gt=0)
    memory_max_set_members: int = Field(default=50_000, gt=0)
    processed_item_ttl_hours: int | None = Field(default=24, ge=1)
    source_health_drift_ratio: float = Field(default=0.2, ge=0.0, le=1.0)
    source_health_min_baseline: float = Field(default=3.0, ge=0.0)
    source_health_failure_streak_pause: int = Field(default=3, gt=0)
    source_health_probe_every_n_runs: int = Field(default=5, gt=0)
    source_pool_dynamic_enabled: bool = True
    source_soft_deadline_seconds: float = Field(default=45.0, gt=0.0, le=3600.0)
    source_hard_deadline_seconds: float = Field(default=120.0, gt=0.0, le=7200.0)
    source_overflow_concurrency: int = Field(default=2, gt=0, le=16)
    source_hard_cancel_grace_seconds: float = Field(default=0.1, ge=0.0, le=60.0)
    source_eviction_pause_threshold: int = Field(default=3, ge=1)
    source_pool_adaptive_resize: bool = False
    source_fetch_concurrency_max: int = Field(default=16, gt=0, le=64)
    source_assessment_ttl_days: int = Field(default=7, gt=0)
    career_site_window_max_details: int = Field(default=500, gt=0)
    scheduler_jitter_seconds: float = Field(default=0.0, ge=0.0)
    job_backend: str = "sqlite"
    search_backend: str = "sqlite"
    job_store_path: Path | None = None
    search_language: str = "simple"
    embedding_enabled: bool = False
    embedding_prefilter_enabled: bool = False
    embedding_prefilter_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    # Relevance backend for SemanticPrefilterNode. "keywords" = legacy YAML
    # token-overlap; "shots" = DB-backed example-posting embeddings (Qdrant
    # collection profile_shots_e5, local multilingual model). Default keeps
    # existing behaviour; opt into the shot-anchor with JOB_FTCH_RELEVANCE_BACKEND=shots.
    relevance_backend: str = "keywords"
    relevance_shot_collection: str = "profile_shots_e5"
    relevance_shot_model: str = "intfloat/multilingual-e5-small"
    # 0.05 = eval optimum on dense shot margin (P=0.875/R=0.761/F1=0.814,
    # 400/seed42, beats historical gold). Cuts junk before the LLM judge.
    relevance_shot_threshold: float = 0.05
    relevance_prompt_path: str = "fixtures/shots/generated_relevance_prompt.txt"
    # BGE-M3 single-encoder path (Phase 1). When enabled, replaces MiniLM prefilter
    # and uses BGE-M3 dense vectors for shot-anchor scoring.
    bgem3_enabled: bool = False
    bgem3_model: str = "BAAI/bge-m3"
    relevance_shot_collection_bgem3: str = "profile_shots_bgem3_mvp_v3"
    relevance_shot_seed_collection_bgem3: str = "profile_shots_bgem3_seed"
    # BGE-M3 shot store backend. "qdrant" (default) persists shots
    # in a Qdrant collection (``profile_shots_bgem3``) so the
    # relevance scorer at pipeline time reads user-added examples
    # directly. The bot upserts on every add/delete. "memory" keeps
    # shots only in the in-process registry (no persistence between
    # restarts; useful for tests and short-lived CLI runs).
    relevance_shot_backend: str = "qdrant"
    # Source-of-truth selector for late-stage relevance shots.
    # user_db_only: user-scoped live shots only
    # tenant_qdrant_only: tenant-scoped live shots
    # seed_fixture_only: benchmark seed collection only
    # mixed_debug: live tenant shots with seed fallback when empty
    relevance_shot_source_mode: str = "user_db_only"
    extraction_min_hiring_intent: float = Field(default=0.0, ge=0.0, le=1.0)
    language_detection_enabled: bool = False
    translation_enabled: bool = False
    translation_target_language: str = "ru"
    llm_relevance_low_threshold: float = Field(default=0.2, ge=0.0, le=1.0)
    # Must be >= routing_accept_threshold (0.55) to avoid a dead band where
    # items score above the judge window but below the routing accept floor.
    llm_relevance_high_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    llm_relevance_max_per_run: int = Field(default=500, ge=0)
    llm_presentable_enabled: bool = True
    llm_presentable_max_per_run: int = Field(default=50, ge=0)
    llm_ontology_max_per_shot: int = Field(default=1, ge=0)
    ontology_compiler_prompt_path: str = "config/prompts/ontology_compiler_v2.yaml"
    ontology_compiler_mode: str = "llm_v2_apply"
    ontology_compiler_model: str = "gpt-4.1-nano"
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int | None = None
    embedding_upsert_batch_size: int = Field(default=32, gt=0, le=512)
    vector_backend: str | None = None
    qdrant_url: str | None = None
    qdrant_api_key: SecretStr | None = None
    qdrant_collection: str = "job_ftch_jobs"
    ollama_base_url: str = "http://localhost:11434"

    model_config = SettingsConfigDict(
        env_file=_resolve_env_files(),
        env_prefix="JOB_FTCH_",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Precedence (highest wins): init kwargs > env vars > dotenv > runtime YAML > secrets."""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=_resolve_runtime_config_files()),
            file_secret_settings,
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
    def validate_store_backend(self) -> Settings:
        """Validate `store_backend` choice (per ADR-034).

        With `store_backend='auto'` (the new default), Settings() can be
        constructed without any env vars. The actual backend is resolved by
        `application.registry.resolve_store_backend(settings)` at run time,
        and resolved to one of the registered backends ("memory", "sqlite",
        "postgres") by checking (1) DSN availability and (2) filesystem
        writability for the SQLite path. Production operators should still
        set `JOB_FTCH_STORE_BACKEND=postgres` plus `JOB_FTCH_STORE_DSN=...`
        explicitly; an INFO log line records the auto-resolution on every run.
        """
        if self.store_backend == "postgres" and not self.store_dsn:
            import logging

            logging.getLogger(__name__).warning(
                "store_backend='postgres' but STORE_DSN is empty; "
                "consider setting JOB_FTCH_STORE_BACKEND=auto for dev "
                "or supplying a DSN. The auto-resolver will pick memory/sqlite."
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
        "http_proxy_list",
        "proxy_rescue_allow_domains",
        "proxy_rescue_deny_domains",
        "captcha_authorized_domains",
        mode="before",
    )
    @classmethod
    def parse_comma_separated_list(cls, value: object) -> list[str]:
        if not value:
            return []
        if isinstance(value, str):
            return [p.strip() for p in value.split(",") if p.strip()]
        if isinstance(value, list):
            return [str(p).strip() for p in value if str(p).strip()]
        return []

    @field_validator(
        "telegram_entity",
        "openai_model",
        "openai_base_url",
        "telegram_publish_entity",
        "output_schema_version",
        "quarantine_output_schema_version",
        "review_output_schema_version",
        "rejected_output_schema_version",
        "vector_backend",
        "qdrant_url",
        "qdrant_collection",
        "ollama_base_url",
        "embedding_model",
        "search_language",
        "translation_target_language",
        "tenant_id",
        "tenant_display_name",
        "telegram_proxy_type",
        "telegram_proxy_host",
        "telegram_proxy_username",
        "career_site_url",
    )
    @classmethod
    def strip_optional_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator(
        "telegram_api_hash",
        "telegram_proxy_password",
        "openai_api_key",
        "langfuse_secret_key",
        "qdrant_api_key",
        "store_dsn",
    )
    @classmethod
    def strip_optional_secrets(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        stripped = value.get_secret_value().strip()
        return SecretStr(stripped) if stripped else None

    @model_validator(mode="after")
    def validate_telegram_jitter(self) -> Settings:
        if self.telegram_jitter_min_seconds > self.telegram_jitter_max_seconds:
            raise ValueError(
                "telegram_jitter_min_seconds cannot be greater than telegram_jitter_max_seconds."
            )
        return self

    @model_validator(mode="after")
    def validate_relevance_window(self) -> Settings:
        if self.llm_relevance_high_threshold < self.routing_accept_threshold:
            msg = (
                "llm_relevance_high_threshold cannot be lower than "
                "routing_accept_threshold; this creates a dead band."
            )
            raise ValueError(msg)
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


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
