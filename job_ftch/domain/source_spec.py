"""Source configuration models — what to fetch, never how to authenticate."""

from __future__ import annotations

import datetime as dt  # noqa: TC003
from typing import Annotated, Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]

_SECRET_CONFIG_MARKERS = (
    "password",
    "secret",
    "token",
    "cookie",
    "authorization",
    "api_key",
    "apikey",
    "proxy_url",
    "proxyurl",
    "credential",
    "dsn",
)


def _validate_secret_free_config(config: dict[str, Any]) -> dict[str, Any]:
    """Reject credentials at every depth of declarative source configuration."""

    def visit(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized = str(key).lower().replace("-", "_")
                child_path = f"{path}.{key}" if path else str(key)
                if any(marker in normalized for marker in _SECRET_CONFIG_MARKERS):
                    raise ValueError(
                        f"Configuration key {child_path!r} may contain credentials. "
                        "Use auth_source_id or runtime environment variables instead."
                    )
                visit(nested, child_path)
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                visit(nested, f"{path}[{index}]")

    visit(config)
    return config


class BaseSourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    interval_seconds: int | None = None
    rate_limit_min_interval_seconds: float = 0.0
    rate_limit_backoff_multiplier: float = 2.0
    ingest_mode: str = "polling"
    bypass: str | None = None  # registered bypass strategy name, e.g. "proxy"
    bypass_config: dict[str, JsonValue] = Field(default_factory=dict)
    initial_ingest_mode: Literal["auto", "max_items", "lookback_window"] = "auto"
    initial_ingest_max_items: int = Field(default=50, gt=0)
    initial_ingest_lookback_seconds: int = Field(default=7 * 24 * 60 * 60, gt=0)
    freshness_cutoff_utc: dt.datetime | None = None  # runtime overlay, not a static capability

    @field_validator("bypass_config")
    @classmethod
    def _validate_bypass_config(cls, bypass_config: dict[str, Any]) -> dict[str, Any]:
        return _validate_secret_free_config(bypass_config)


class TelegramChannelSpec(BaseSourceSpec):
    type: Literal["telegram_channel"] = "telegram_channel"
    entity: str = Field(min_length=1)
    limit: int | None = Field(default=None, gt=0)
    auth_source_id: str | None = None
    source_name: str | None = None  # override display name


class TelegramGroupSpec(BaseSourceSpec):
    type: Literal["telegram_group"] = "telegram_group"
    entity: str = Field(min_length=1)
    limit: int | None = Field(default=None, gt=0)
    auth_source_id: str | None = None
    source_name: str | None = None


class TelegramCommentsSpec(BaseSourceSpec):
    type: Literal["telegram_comments"] = "telegram_comments"
    entity: str = Field(min_length=1)
    post_limit: int | None = Field(default=None, gt=0)
    comment_limit_per_post: int | None = Field(default=None, gt=0)
    auth_source_id: str | None = None
    source_name: str | None = None


class DeclarativeHtmlSpec(BaseSourceSpec):
    type: Literal["declarative_html"] = "declarative_html"
    url: str = Field(min_length=1)
    parser_kind: str = "auto"  # "auto", "greenhouse", or any registered parser kind
    limit: int | None = Field(default=None, gt=0)
    source_name: str | None = None


class CareerSiteSpec(BaseSourceSpec):
    type: Literal["career_site"] = "career_site"
    url: str = Field(min_length=1)
    limit: int | None = Field(default=None, gt=0)
    source_name: str | None = None
    monitor: str | None = "auto"  # registered monitor name, or "auto" for auto-detect
    monitor_config: dict[str, Any] = Field(default_factory=dict)  # passed to monitor
    scraper: str | None = None  # registered scraper name; None = auto from monitor
    scraper_config: dict[str, Any] = Field(default_factory=dict)  # passed to scraper
    site_parser: str | None = None  # explicit site-parser pin; default remains URL-bound
    search_locked: bool = False  # keep an operator-authored query unchanged
    scraper_fallback: list[str] = Field(default_factory=list)  # fallback scraper chain
    detail_limit: int | None = None  # max detail pages to scrape (None = unlimited)
    url_filter: str | dict[str, Any] | None = None  # regex or {include, exclude}
    url_transform: dict[str, Any] | None = None  # {find, replace} regex rewrite

    @field_validator("monitor_config", "scraper_config")
    @classmethod
    def _validate_configs(cls, config: dict[str, Any]) -> dict[str, Any]:
        return _validate_secret_free_config(config)


class LocalFixtureSpec(BaseSourceSpec):
    type: Literal["local_fixture"] = "local_fixture"
    path: str = Field(min_length=1)
    source_name: str | None = None


# RM-098: RestAPISourceSpec
class CursorPagination(BaseModel):
    type: Literal["cursor"] = "cursor"
    cursor_param: str
    next_cursor_path: str  # JSON path to next cursor in response


class OffsetPagination(BaseModel):
    type: Literal["offset"] = "offset"
    offset_param: str = "offset"
    limit_param: str = "limit"
    page_size: int = 20


class LinkHeaderPagination(BaseModel):
    type: Literal["link_header"] = "link_header"
    rel: str = "next"


PaginationConfig = Annotated[
    CursorPagination | OffsetPagination | LinkHeaderPagination,
    Field(discriminator="type"),
]


class RestAPISourceSpec(BaseSourceSpec):
    type: Literal["rest_api"] = "rest_api"
    base_url: AnyHttpUrl
    jobs_endpoint: str
    pagination: PaginationConfig | None = None
    field_map: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, str] = Field(default_factory=dict)
    incremental_cursor_field: str | None = None
    source_name: str | None = None
    auth_source_id: str | None = None

    @field_validator("headers")
    @classmethod
    def _validate_headers(cls, headers: dict[str, str]) -> dict[str, str]:
        for k in headers:
            normalized = k.lower().replace("-", "_")
            if any(marker in normalized for marker in _SECRET_CONFIG_MARKERS):
                raise ValueError(f"Header {k!r} is a credential. Use auth_source_id instead.")
        return headers


class BrowserSourceSpec(BaseSourceSpec):
    type: Literal["browser"] = "browser"
    url: AnyHttpUrl
    parser: str = "generic"
    source_name: str | None = None


class RSSFeedSourceSpec(BaseSourceSpec):
    type: Literal["rss_feed"] = "rss_feed"
    feed_url: AnyHttpUrl
    incremental: bool = True
    source_name: str | None = None


class TelegramRealtimeSourceSpec(BaseSourceSpec):
    type: Literal["telegram_realtime"] = "telegram_realtime"
    entity: str = Field(min_length=1)
    auth_source_id: str | None = None
    source_name: str | None = None


class LeverSourceSpec(BaseSourceSpec):
    type: Literal["lever"] = "lever"
    company: str = Field(min_length=1, description="Lever company slug, e.g. 'acme'")
    source_name: str | None = None


class WebhookSourceSpec(BaseSourceSpec):
    type: Literal["webhook"] = "webhook"
    path: str = "/webhook"
    host: str = "0.0.0.0"  # nosec B104 - explicit webhook listener configuration
    port: int = 8080
    event_id_field: str = "id"
    source_name: str | None = None


class WebSocketSourceSpec(BaseSourceSpec):
    type: Literal["websocket"] = "websocket"
    url: str = Field(min_length=1)
    event_id_field: str = "id"
    source_name: str | None = None


SourceSpec = Annotated[
    TelegramChannelSpec
    | TelegramGroupSpec
    | TelegramCommentsSpec
    | DeclarativeHtmlSpec
    | CareerSiteSpec
    | LocalFixtureSpec
    | RestAPISourceSpec
    | BrowserSourceSpec
    | RSSFeedSourceSpec
    | TelegramRealtimeSourceSpec
    | LeverSourceSpec
    | WebhookSourceSpec
    | WebSocketSourceSpec,
    Field(discriminator="type"),
]
