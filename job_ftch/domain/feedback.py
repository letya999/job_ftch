"""Reader verdicts on published vacancies.

A published card is the only place where a human sees the pipeline's final answer, so it
is also the cheapest place to collect a correction. These records are evidence about the
profile boundary, not a routing decision: nothing here changes a vacancy that already
shipped.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FeedbackVerdict(StrEnum):
    """Why a reader flagged a published vacancy."""

    OFF_PROFILE = "off_profile"


class FeedbackAudience(StrEnum):
    """Who may flag a published vacancy.

    A public channel and a private one need different answers: opening the button to
    every reader collects more signal but also invites noise from people who do not know
    the profile, so the owner chooses.
    """

    OFF = "off"
    ADMIN = "admin"
    ALL = "all"

    @property
    def collects(self) -> bool:
        """True when cards should carry the button at all."""
        return self is not FeedbackAudience.OFF

    @property
    def label(self) -> str:
        return {
            FeedbackAudience.OFF: "выключена",
            FeedbackAudience.ADMIN: "только админы",
            FeedbackAudience.ALL: "все читатели канала",
        }[self]


class VacancyFeedback(BaseModel):
    """One reader verdict about one published vacancy.

    Identity is (tenant_id, job_id, user_id): a second press by the same reader on the
    same card is the same opinion, not a second vote.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    verdict: FeedbackVerdict = FeedbackVerdict.OFF_PROFILE
    title: str = ""
    url: str = ""
    source_name: str = ""
    excerpt: str = Field(default="", max_length=4000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.tenant_id, self.job_id, self.user_id)


class FeedbackJobTally(BaseModel):
    """How many distinct readers flagged one vacancy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str = Field(min_length=1)
    title: str = ""
    url: str = ""
    source_name: str = ""
    votes: int = Field(ge=1)
    excerpt: str = ""


class FeedbackSummary(BaseModel):
    """Aggregated feedback used to decide whether the profile needs correcting."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(min_length=1)
    total: int = Field(default=0, ge=0)
    distinct_jobs: int = Field(default=0, ge=0)
    by_source: dict[str, int] = Field(default_factory=dict)
    top_jobs: tuple[FeedbackJobTally, ...] = ()

    @property
    def is_empty(self) -> bool:
        return self.total == 0
