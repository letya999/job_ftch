"""Source adapter implementations."""

from job_ftch.infrastructure.sources.career_site import (
    CareerSiteSource as LegacyCareerSiteSource,
)
from job_ftch.infrastructure.sources.career_site_source import CareerSiteSource
from job_ftch.infrastructure.sources.declarative import (
    CareerSiteConfig,
    DeclarativeCareerSiteSource,
)
from job_ftch.infrastructure.sources.local_fixture import LocalFixtureSource
from job_ftch.infrastructure.sources.telegram import (
    TelegramChannelSource,
    TelegramCommentSource,
    TelegramGroupSource,
)

__all__ = [
    "CareerSiteSource",
    "LegacyCareerSiteSource",
    "CareerSiteConfig",
    "DeclarativeCareerSiteSource",
    "LocalFixtureSource",
    "TelegramChannelSource",
    "TelegramCommentSource",
    "TelegramGroupSource",
]
