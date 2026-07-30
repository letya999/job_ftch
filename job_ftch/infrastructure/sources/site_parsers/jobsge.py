"""Site-specific parser for jobs.ge (Georgian job board).

SSR site with table-based layout. Jobs are in <tr> rows with columns:
title, company, date posted, deadline. Links: /ge/?view=jobs&id=XXXXXX.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


def _clean_text(value: str) -> str:
    return " ".join(value.split())


class JobsGeParser:
    """Parser for jobs.ge — Georgian job board with SSR table layout."""

    domain_pattern = r"^https?://(?:www\.)?jobs\.ge(?:/|$)"
    has_custom_parse = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False,
            wait="domcontentloaded",
            include_if_detail_page=False,
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        limit = spec.limit or 100
        source_name = spec.source_name or "jobs_ge"

        from job_ftch.infrastructure.sources.source_deadline import await_with_source_deadline

        response = await await_with_source_deadline(client.get(spec.url, follow_redirects=True))
        html = response.text

        parser = LexborHTMLParser(html)
        items: list[RawItem] = []

        # jobs.ge uses table rows for job listings
        # Each table has <tr> with cells: [icon, title, salary?, company, posted, deadline]
        for table in parser.css("table"):
            rows = table.css("tr")
            for row in rows:
                cells = row.css("td")
                if len(cells) < 3:
                    continue

                # Find the job link (first <a> with view=jobs&id= pattern)
                link = row.css_first('a[href*="view=jobs"]')
                if not link:
                    continue

                href = link.attributes.get("href")
                if not href:
                    continue

                job_url = urljoin(spec.url, href)
                title = _clean_text(link.text(separator=" ", strip=True))
                if not title or len(title) < 3:
                    continue

                # Extract company name (4th cell, index 3 if exists)
                company = ""
                if len(cells) >= 4:
                    company = _clean_text(cells[3].text(separator=" ", strip=True))

                # Extract dates (last cells)
                posted = ""
                deadline = ""
                if len(cells) >= 6:
                    posted = _clean_text(cells[4].text(separator=" ", strip=True))
                    deadline = _clean_text(cells[5].text(separator=" ", strip=True))

                text_parts = [title]
                if company:
                    text_parts.append(f"Company: {company}")
                if posted:
                    text_parts.append(f"Posted: {posted}")
                if deadline:
                    text_parts.append(f"Deadline: {deadline}")

                # Extract job ID from URL for external_id
                external_id = href
                if "id=" in href:
                    external_id = href.split("id=")[-1].split("&")[0]

                items.append(
                    build_raw_item(
                        source_kind=SourceKind.CAREER_SITE,
                        source_name=source_name,
                        external_id=f"jobs_ge_{external_id}",
                        url=job_url,
                        text="\n".join(text_parts),
                        metadata={
                            "board_url": spec.url,
                            "job_url": job_url,
                            "company": company or None,
                            "location": "Georgia",
                            "posted": posted or None,
                            "deadline": deadline or None,
                            "parser": "jobsge",
                        },
                    )
                )

                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break

        for item in items:
            yield item

    @property
    def __name__(self) -> str:
        return "JobsGeParser"


register_site_parser(
    "jobs_ge",
    domain_pattern=JobsGeParser.domain_pattern,
)(JobsGeParser)
