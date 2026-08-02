"""Parser for Careerspace public SSR job listings."""

from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import is_challenge_response

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


_JOBS_URL = "https://careerspace.app/jobs"
_NUXT_RE = re.compile(r"window\.__NUXT__=\(function\((?P<names>[^)]*)\)\{(?P<body>.*?)\}\((?P<args>.*?)\)\);", re.S)


def _decode_js_string(value: str) -> str:
    try:
        return str(ast.literal_eval(value))
    except (SyntaxError, ValueError):
        return value.strip("\"'")


def _split_top_level(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escape = False
    for index, char in enumerate(value):
        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char in "([{":
            depth += 1
            continue
        if char in ")]}":
            depth -= 1
            continue
        if char == "," and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    tail = value[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _js_value(raw: str, aliases: dict[str, Any]) -> Any:
    raw = raw.strip()
    if raw in aliases:
        return aliases[raw]
    if raw == "null" or raw.startswith("void "):
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw.startswith('"') or raw.startswith("'"):
        return _decode_js_string(raw)
    try:
        return int(raw)
    except ValueError:
        return raw


def _extract_bracketed(value: str, marker: str) -> str | None:
    start = value.find(marker)
    if start < 0:
        return None
    open_index = value.find("[", start)
    if open_index < 0:
        return None
    depth = 0
    quote: str | None = None
    escape = False
    for index in range(open_index, len(value)):
        char = value[index]
        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return value[open_index + 1 : index]
    return None


def _extract_raw_field(chunk: str, field: str) -> str | None:
    match = re.search(rf"\b{re.escape(field)}:([^,}}]+)", chunk)
    if not match:
        return None
    start = match.start(1)
    depth = 0
    quote: str | None = None
    escape = False
    for index in range(start, len(chunk)):
        char = chunk[index]
        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char in "([{":
            depth += 1
            continue
        if char in ")]}":
            if depth == 0:
                return chunk[start:index].strip()
            depth -= 1
            continue
        if char == "," and depth == 0:
            return chunk[start:index].strip()
    return chunk[start:].strip()


def _extract_field(chunk: str, field: str, aliases: dict[str, Any]) -> Any:
    raw = _extract_raw_field(chunk, field)
    if raw is None:
        return None
    return _js_value(raw, aliases)


def _extract_nested_name(chunk: str, container: str, aliases: dict[str, Any]) -> str | None:
    match = re.search(rf"\b{re.escape(container)}:\[\{{[^}}]*\bname:([^,}}]+)", chunk)
    if not match:
        return None
    value = _js_value(match.group(1), aliases)
    return str(value) if value else None


def _extract_jobs(html: str) -> list[dict[str, Any]]:
    match = _NUXT_RE.search(html)
    if not match:
        return []
    names = [name.strip() for name in match.group("names").split(",") if name.strip()]
    raw_args = _split_top_level(match.group("args"))
    aliases = {
        name: _js_value(raw_args[index], {})
        for index, name in enumerate(names)
        if index < len(raw_args)
    }
    jobs_payload = _extract_bracketed(match.group("body"), "jobs:{jobs:[")
    if not jobs_payload:
        return []
    rows: list[dict[str, Any]] = []
    for raw_chunk in _split_top_level(jobs_payload):
        chunk = raw_chunk.strip()
        if not chunk.startswith("{"):
            continue
        job_id = _extract_field(chunk, "job_id", aliases)
        title = _extract_field(chunk, "job_name", aliases)
        company = _extract_field(chunk, "company_name", aliases)
        salary_from = _extract_field(chunk, "job_salary_from", aliases)
        salary_to = _extract_field(chunk, "job_salary_to", aliases)
        currency = _extract_field(chunk, "job_salary_currency", aliases)
        city = _extract_nested_name(chunk, "cities", aliases)
        country = _extract_nested_name(chunk, "countries", aliases)
        if not job_id or not title:
            continue
        rows.append(
            {
                "id": job_id,
                "title": str(title),
                "company": str(company) if company else None,
                "salary_from": salary_from,
                "salary_to": salary_to,
                "currency": currency,
                "city": city,
                "country": country,
            }
        )
    return rows


class CareerspaceParser:
    """Extract jobs from Careerspace's Nuxt SSR state."""

    domain_pattern = r"^https?://careerspace\.app(?:/|$)"
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
        response = await client.get(_JOBS_URL, follow_redirects=True)
        if response.status_code >= 400 or is_challenge_response(response.text):
            response.raise_for_status()
        for row in _extract_jobs(response.text)[: spec.limit or 50]:
            job_url = f"{_JOBS_URL}/{row['id']}"
            text_parts = [
                row["title"],
                row.get("company"),
                ", ".join(part for part in (row.get("city"), row.get("country")) if part),
            ]
            salary_from = row.get("salary_from")
            salary_to = row.get("salary_to")
            currency = row.get("currency")
            if salary_from or salary_to:
                text_parts.append(f"{salary_from or ''}-{salary_to or ''} {currency or ''}".strip())
            yield build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=spec.source_name or "careerspace",
                external_id=str(row["id"]),
                url=job_url,
                text="\n".join(part for part in text_parts if part),
                metadata={
                    "board_url": _JOBS_URL,
                    "job_url": job_url,
                    "company": row.get("company"),
                    "location": ", ".join(
                        part for part in (row.get("city"), row.get("country")) if part
                    )
                    or None,
                    "salary_from": salary_from,
                    "salary_to": salary_to,
                    "currency": currency,
                    "parser": "careerspace_nuxt_ssr",
                },
            )


register_site_parser("careerspace", domain_pattern=CareerspaceParser.domain_pattern)(
    CareerspaceParser
)
