from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

# Trigger loading
from . import (
    ashby as ashby,
    breezy as breezy,
    deel as deel,
    dom as dom,
    eightfold as eightfold,
    greenhouse as greenhouse,
    join as join,
    lever as lever,
    nextdata as nextdata,
    personio as personio,
    recruitee as recruitee,
    rippling as rippling,
    rss_board as rss_board,
    sitemap as sitemap,
    smartrecruiters as smartrecruiters,
    softgarden as softgarden,
    workable as workable,
    workday as workday,
)

if TYPE_CHECKING:
    from job_ftch.application.registry import register_monitor


def load_monitors() -> None:
    """Import all monitors to trigger registration."""
    for module_name in (
        "job_ftch.infrastructure.sources.monitors.greenhouse",
        "job_ftch.infrastructure.sources.monitors.lever",
        "job_ftch.infrastructure.sources.monitors.sitemap",
        "job_ftch.infrastructure.sources.monitors.dom",
        "job_ftch.infrastructure.sources.monitors.nextdata",
        "job_ftch.infrastructure.sources.monitors.ashby",
        "job_ftch.infrastructure.sources.monitors.workday",
        "job_ftch.infrastructure.sources.monitors.smartrecruiters",
        "job_ftch.infrastructure.sources.monitors.breezy",
        "job_ftch.infrastructure.sources.monitors.recruitee",
        "job_ftch.infrastructure.sources.monitors.personio",
        "job_ftch.infrastructure.sources.monitors.rss_board",
        "job_ftch.infrastructure.sources.monitors.rippling",
        "job_ftch.infrastructure.sources.monitors.workable",
        "job_ftch.infrastructure.sources.monitors.deel",
        "job_ftch.infrastructure.sources.monitors.softgarden",
        "job_ftch.infrastructure.sources.monitors.join",
        "job_ftch.infrastructure.sources.monitors.eightfold",
    ):
        try:
            import_module(module_name)
        except ImportError:
            # Skip optional monitors if dependencies are missing
            continue
