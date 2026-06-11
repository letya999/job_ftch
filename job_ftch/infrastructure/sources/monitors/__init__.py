from __future__ import annotations

from typing import TYPE_CHECKING

# Trigger loading
from . import (
    api_sniffer as api_sniffer,
)
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

if TYPE_CHECKING:
    from job_ftch.application.registry import register_monitor as register_monitor
