from __future__ import annotations

from importlib import import_module

# Trigger loading
from . import (
    ashby as ashby,
)
from . import (
    breezy as breezy,
)
from . import (
    deel as deel,
)
from . import (
    dom as dom,
)
from . import (
    eightfold as eightfold,
)
from . import (
    greenhouse as greenhouse,
)
from . import (
    join as join,
)
from . import (
    lever as lever,
)
from . import (
    nextdata as nextdata,
)
from . import (
    personio as personio,
)
from . import (
    recruitee as recruitee,
)
from . import (
    rippling as rippling,
)
from . import (
    rss_board as rss_board,
)
from . import (
    sitemap as sitemap,
)
from . import (
    smartrecruiters as smartrecruiters,
)
from . import (
    softgarden as softgarden,
)
from . import (
    workable as workable,
)
from . import (
    workday as workday,
)


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
