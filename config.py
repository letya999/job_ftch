"""Application configuration via pydantic-settings."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Pipeline settings loaded from environment variables."""

    store_backend: str = "memory"
    log_level: str = "DEBUG"
    pipeline_max_items_per_run: int = 200

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
