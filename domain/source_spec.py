"""Source configuration models — what to fetch, never how to authenticate."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class TelegramChannelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["telegram_channel"] = "telegram_channel"
    entity: str = Field(min_length=1)
    limit: int = Field(default=100, gt=0)
    auth_source_id: str | None = None
    source_name: str | None = None  # override display name


class TelegramGroupSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["telegram_group"] = "telegram_group"
    entity: str = Field(min_length=1)
    limit: int = Field(default=100, gt=0)
    auth_source_id: str | None = None
    source_name: str | None = None


class TelegramCommentsSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["telegram_comments"] = "telegram_comments"
    entity: str = Field(min_length=1)
    post_limit: int = Field(default=20, gt=0)
    comment_limit_per_post: int = Field(default=50, gt=0)
    auth_source_id: str | None = None
    source_name: str | None = None


class DeclarativeHtmlSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["declarative_html"] = "declarative_html"
    url: str = Field(min_length=1)
    parser_kind: str = "auto"  # "auto", "greenhouse", or any registered parser kind
    limit: int = Field(default=100, gt=0)
    source_name: str | None = None


class CareerSiteSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["career_site"] = "career_site"
    url: str = Field(min_length=1)
    limit: int = Field(default=100, gt=0)
    source_name: str | None = None


class LocalFixtureSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["local_fixture"] = "local_fixture"
    path: str = Field(min_length=1)
    source_name: str | None = None


SourceSpec = Annotated[
    TelegramChannelSpec
    | TelegramGroupSpec
    | TelegramCommentsSpec
    | DeclarativeHtmlSpec
    | CareerSiteSpec
    | LocalFixtureSpec,
    Field(discriminator="type"),
]
