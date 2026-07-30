"""PresentableJob domain model — structured output of LLM point ③.

Used by ``PresentableTextNode`` to format a JobRecord as clean Markdown
suitable for a Telegram channel post.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PresentableJob(BaseModel):
    """LLM-formatted job posting for Telegram bot.

    Free-form text fields (``title``, ``body``, ``salary_formatted``,
    ``location_formatted``, ``contact_section``) are in the language of
    the input job text.
    """

    model_config = ConfigDict(extra="forbid")

    title: str
    body: str
    salary_formatted: str | None = None
    location_formatted: str | None = None
    contact_section: str | None = None
    tags: tuple[str, ...] = ()
    ats_score: float = Field(ge=0.0, le=1.0)
    language: str
