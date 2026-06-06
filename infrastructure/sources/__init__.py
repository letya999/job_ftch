"""Source adapter implementations."""

from infrastructure.sources.career_site import CareerSiteSource
from infrastructure.sources.local_fixture import LocalFixtureSource
from infrastructure.sources.telegram import (
    TelegramChannelSource,
    TelegramCommentSource,
    TelegramGroupSource,
)

__all__ = [
    "CareerSiteSource",
    "LocalFixtureSource",
    "TelegramChannelSource",
    "TelegramCommentSource",
    "TelegramGroupSource",
]
