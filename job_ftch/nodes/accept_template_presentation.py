"""Zero-cost presentation for accepted vacancies."""

from __future__ import annotations

from job_ftch.domain import JobRecord, MatchDecision
from job_ftch.nodes.presentable_text import _template_present


class AcceptTemplatePresentationNode:
    """Create a deterministic card only after the routing decision is ACCEPT."""

    async def process(self, item: JobRecord) -> JobRecord:
        if item.routing_decision is not MatchDecision.ACCEPT or item.presentable is not None:
            return item
        return item.model_copy(update={"presentable": _template_present(item)})
