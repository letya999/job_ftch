"""Direct Jibe JSON ingestion for the Publicis Groupe career board."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    keywords_from_spec,
    normalize_search_keywords,
    text_matches_keywords,
    with_query_params,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


class PublicisCareerParser:
    """Use the public Jibe endpoint instead of browser/detail retries."""

    domain_pattern = r"^https?://careers\.publicisgroupe\.com/jobs(?:[/?#]|$)"
    has_custom_parse = True
    supports_discover = False
    supports_search = True
    search_mode = "combined"
    confirmed_empty_on_empty = True
    terminal_on_error = True
    _API_PATH = "/api/jobs"

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False,
            wait="domcontentloaded",
            include_if_detail_page=False,
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return "publicis_jibe"

    def build_search_urls(
        self, base_url: str, keywords: Any, *, limit: int | None = None
    ) -> list[str]:
        del limit
        terms = normalize_search_keywords(keywords)
        return [with_query_params(base_url, {"keywords": " ".join(terms)})] if terms else [base_url]

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        parsed = urlsplit(spec.url)
        api_url = urlunsplit((parsed.scheme, parsed.netloc, self._API_PATH, "", ""))
        keywords = keywords_from_spec(spec)
        limit = spec.limit or 50
        page = 1
        emitted = 0
        seen: set[str] = set()

        while emitted < limit:
            params: dict[str, str | int] = {"limit": min(limit - emitted, 100), "page": page}
            if keywords:
                params["keywords"] = " ".join(keywords)
            response = await client.get(api_url, params=params, follow_redirects=True)
            response.raise_for_status()
            payload = response.json()
            jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
            if not isinstance(jobs, list) or not jobs:
                return
            for job in jobs:
                data = job.get("data") if isinstance(job, dict) else None
                if not isinstance(data, dict):
                    continue
                title = str(data.get("title") or "").strip()
                external_id = str(data.get("req_id") or data.get("slug") or "").strip()
                if not title or not external_id or external_id in seen:
                    continue
                text = "\n".join(
                    value
                    for value in (
                        title,
                        str(data.get("description") or "").strip(),
                        str(data.get("responsibilities") or "").strip(),
                        str(data.get("qualifications") or "").strip(),
                    )
                    if value
                )
                if keywords and not text_matches_keywords(text, keywords):
                    continue
                seen.add(external_id)
                meta_data = data.get("meta_data")
                canonical_url = (
                    str(meta_data.get("canonical_url") or "") if isinstance(meta_data, dict) else ""
                )
                url = canonical_url or f"{spec.url.rstrip('/')}/{external_id}"
                yield build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=spec.source_name or "publicis",
                    external_id=external_id,
                    url=url,
                    text=text,
                    metadata={
                        "board_url": spec.url,
                        "parser": "publicis_jibe_api",
                        "observation_kind": "vacancy_detail",
                        "company": str(data.get("hiring_organization") or "Publicis Groupe"),
                        "company_authoritative": True,
                        "date_posted": data.get("posted_date"),
                        "apply_url": data.get("apply_url"),
                        "location": data.get("full_location") or data.get("short_location"),
                    },
                )
                emitted += 1
                if emitted >= limit:
                    return
            page += 1


register_site_parser(
    "publicis_jibe",
    domain_pattern=PublicisCareerParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:careers.publicisgroupe.com",
        has_stable_id=True,
        has_stable_url=True,
        has_publication_time=True,
        requires_full_snapshot=False,
        rationale="Publicis exposes a public Jibe JSON jobs endpoint; no browser detail retry is needed.",
    ),
)(PublicisCareerParser)
