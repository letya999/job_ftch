"""Prototype classifier to determine if a text is a job vacancy."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_ftch.domain import JobRecord


class IsJobNode:
    """
    Stage[JobRecord, JobRecord] — prototype classifier to determine if item is a job posting.
    """

    def __init__(self) -> None:
        # Very simple prototype heuristics
        self._keywords = re.compile(
            r"(вакансия|требуется|ищем|приглашаем|vacancy|hiring|we are looking for|job)",
            re.IGNORECASE,
        )

    async def process(self, item: JobRecord) -> JobRecord:
        text = (item.title or "") + "\n" + (item.description or "")

        is_job = bool(self._keywords.search(text))

        # Store prototype decision in metadata
        return item.model_copy(
            update={
                "metadata": {
                    **item.metadata,
                    "is_job_prototype": is_job,
                }
            }
        )
