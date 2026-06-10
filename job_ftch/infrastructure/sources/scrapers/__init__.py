from __future__ import annotations

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

if TYPE_CHECKING:
    from job_ftch.application.registry import register_scraper as register_scraper
