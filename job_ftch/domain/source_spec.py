"""Source configuration models — what to fetch, never how to authenticate."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field


class BaseSourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    interval_seconds: int | None = None
    rate_limit_min_interval_seconds: float = 0.0
    rate_limit_backoff_multiplier: float = 2.0
    ingest_mode: str = "polling"
    bypass: str | None = None  # registered bypass strategy name, e.g. "proxy_rotator"
    bypass_config: dict[str, str] = Field(default_factory=dict)


class TelegramChannelSpec(BaseSourceSpec):
    type: Literal["telegram_channel"] = "telegram_channel"
    entity: str = Field(min_length=1)
    limit: int = Field(default=100, gt=0)
    auth_source_id: str | None = None
    source_name: str | None = None  # override display name


class TelegramGroupSpec(BaseSourceSpec):
    type: Literal["telegram_group"] = "telegram_group"
    entity: str = Field(min_length=1)
    limit: int = Field(default=100, gt=0)
    auth_source_id: str | None = None
    source_name: str | None = None


class TelegramCommentsSpec(BaseSourceSpec):
    type: Literal["telegram_comments"] = "telegram_comments"
    entity: str = Field(min_length=1)
    post_limit: int = Field(default=20, gt=0)
    comment_limit_per_post: int = Field(default=50, gt=0)
    auth_source_id: str | None = None
    source_name: str | None = None


class DeclarativeHtmlSpec(BaseSourceSpec):
    type: Literal["declarative_html"] = "declarative_html"
    url: str = Field(min_length=1)
    parser_kind: str = "auto"  # "auto", "greenhouse", or any registered parser kind
    limit: int = Field(default=100, gt=0)
    source_name: str | None = None


class CareerSiteSpec(BaseSourceSpec):
    type: Literal["career_site"] = "career_site"
    url: str = Field(min_length=1)
    limit: int = Field(default=100, gt=0)
    source_name: str | None = None
    monitor: str | None = "auto"  # registered monitor name, or "auto" for auto-detect
    monitor_config: dict = Field(default_factory=dict)  # passed to monitor
    scraper: str | None = None  # registered scraper name; None = auto from monitor
    scraper_config: dict = Field(default_factory=dict)  # passed to scraper
    scraper_fallback: list[str] = Field(default_factory=list)  # fallback scraper chain
    detail_limit: int | None = None  # max detail pages to scrape (None = unlimited)
    url_filter: str | dict | None = None  # regex or {include, exclude}
    url_transform: dict | None = None  # {find, replace} regex rewrite


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
    host: str = "0.0.0.0"  # nosec B104
    port: int = 8080
    source_name: str | None = None


class WebSocketSourceSpec(BaseSourceSpec):
    type: Literal["websocket"] = "websocket"
    url: str = Field(min_length=1)
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
