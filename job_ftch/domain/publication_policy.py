"""Recipient settings for publication, independent of relevance classification."""

from __future__ import annotations

from datetime import date  # noqa: TC003 - Pydantic resolves the field at runtime.
from hashlib import sha256
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, model_validator

CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
Language = Annotated[str, StringConstraints(pattern=r"^[a-z]{2}$")]


class VerifiedEmployer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    languages: tuple[Language, ...] = Field(min_length=1)
    source: HttpUrl
    checked_on: date


class PublicationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    eligible_countries: tuple[CountryCode, ...] = ()
    country_basis: Literal["eligibility", "office"] = "eligibility"
    require_country: bool = False
    posting_languages: tuple[Language, ...] = ()
    company_languages: tuple[Language, ...] = ()
    accept_team_language: bool = False
    verified_employers: dict[str, VerifiedEmployer] = Field(default_factory=dict)
    match: Literal["any", "all"] = "any"
    insufficient_evidence: Literal["review", "skip"] = "review"
    shadow: bool = False

    @model_validator(mode="after")
    def validate_required_country(self) -> PublicationPolicy:
        if self.require_country and not self.eligible_countries:
            raise ValueError("require_country needs eligible_countries")
        return self

    @property
    def version(self) -> str:
        return sha256(self.model_dump_json().encode()).hexdigest()[:16]
