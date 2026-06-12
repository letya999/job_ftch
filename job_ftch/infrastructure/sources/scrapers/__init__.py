from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

# Trigger loading
from . import (
    dom as dom,
)
from . import (
    embedded as embedded,
)
from . import (
    json_ld as json_ld,
)
from . import (
    nextdata as nextdata,
)
from . import (
    rippling as rippling,
)
from . import (
    smartrecruiters as smartrecruiters,
)
from . import (
    workable as workable,
)
from . import (
    workday as workday,
)


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


if TYPE_CHECKING:
    from job_ftch.application.registry import register_scraper as register_scraper
