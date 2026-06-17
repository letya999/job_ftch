"""Site-specific career-site parsers.

Modules here self-register via @register_site_parser. Importing the package
loads all built-in parsers so the registry can resolve them by URL.
"""

from __future__ import annotations

# Import built-in parsers to trigger registration.
from job_ftch.infrastructure.sources.site_parsers import yandex
from job_ftch.infrastructure.sources.site_parsers.base import SiteSpecificParser

__all__ = ["SiteSpecificParser", "yandex"]
