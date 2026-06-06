"""Source adapter implementations."""

<<<<<<< HEAD
from infrastructure.sources.external_source import ExternalJobSource
=======
from infrastructure.sources.career_site import CareerSiteSource
>>>>>>> upstream/dev
from infrastructure.sources.local_fixture import LocalFixtureSource
from infrastructure.sources.telegram import (
    TelegramChannelSource,
    TelegramCommentSource,
    TelegramGroupSource,
)

<<<<<<< HEAD
__all__ = ["LocalFixtureSource", "ExternalJobSource"]
=======
__all__ = [
    "CareerSiteSource",
    "LocalFixtureSource",
    "TelegramChannelSource",
    "TelegramCommentSource",
    "TelegramGroupSource",
]
>>>>>>> upstream/dev
