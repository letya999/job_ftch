from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

# Trigger loading
from . import (
    dom as dom,
    embedded as embedded,
    json_ld as json_ld,
    nextdata as nextdata,
    rippling as rippling,
    smartrecruiters as smartrecruiters,
    workable as workable,
    workday as workday,
)

if TYPE_CHECKING:
    from job_ftch.application.registry import register_scraper


def load_scrapers() -> None:
    """Import all scrapers to trigger registration."""
    for module_name in (
        "job_ftch.infrastructure.sources.scrapers.json_ld",
        "job_ftch.infrastructure.sources.scrapers.embedded",
        "job_ftch.infrastructure.sources.scrapers.nextdata",
        "job_ftch.infrastructure.sources.scrapers.workday",
        "job_ftch.infrastructure.sources.scrapers.smartrecruiters",
        "job_ftch.infrastructure.sources.scrapers.workable",
        "job_ftch.infrastructure.sources.scrapers.rippling",
        "job_ftch.infrastructure.sources.scrapers.dom",
    ):
        try:
            import_module(module_name)
        except ImportError:
            continue
