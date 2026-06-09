"""JSON-LD scraper ported from jobseek."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_scraper
from job_ftch.infrastructure.sources.monitors.shared import normalize_salary_unit
from job_ftch.infrastructure.sources.site_models import ScrapedPostingPayload

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger("job_ftch.scrapers.json_ld")

_CTRL_REPLACEMENTS = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _escape_control_chars_in_strings(raw: str) -> str:
    out: list[str] = []
    in_string = False
    escape = False
    for ch in raw:
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\":
            escape = True
            out.append(ch)
            continue
        if ch == '"':
            in_string = not in_string
        if in_string and ord(ch) < 0x20:
            out.append(_CTRL_REPLACEMENTS.get(ch, ""))
            continue
        out.append(ch)
    return "".join(out)


class _JsonLdExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_jsonld = False
        self._data: list[str] = []
        self.results: list[dict] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            attr_dict = dict(attrs)
            if attr_dict.get("type") == "application/ld+json":
                self._in_jsonld = True
                self._data = []

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._data.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            raw = "".join(self._data).strip()
            if raw:
                try:
                    parsed = json.loads(raw)
                    self.results.append(parsed)
                except json.JSONDecodeError:
                    cleaned = _escape_control_chars_in_strings(raw)
                    try:
                        parsed = json.loads(cleaned)
                        self.results.append(parsed)
                    except json.JSONDecodeError:
                        pass


def _normalize_keys(data: Any) -> Any:
    if isinstance(data, dict):
        out = {}
        for key, value in data.items():
            nk = key if key.startswith("@") else (key[0].lower() + key[1:] if key else key)
            out[nk] = _normalize_keys(value)
        return out
    if isinstance(data, list):
        return [_normalize_keys(item) for item in data]
    return data


def _find_job_posting(data: dict | list) -> dict | None:
    if isinstance(data, list):
        for item in data:
            result = _find_job_posting(item)
            if result:
                return result
        return None

    if isinstance(data, dict):
        type_val = data.get("@type", "")
        if isinstance(type_val, str) and "JobPosting" in type_val:
            return _normalize_keys(data)
        if isinstance(type_val, list) and any("JobPosting" in t for t in type_val):
            return _normalize_keys(data)

        graph = data.get("@graph")
        if isinstance(graph, list):
            return _find_job_posting(graph)

    return None


def _extract_locations(posting: dict) -> list[str] | None:
    locations: list[str] = []
    job_location = posting.get("jobLocation")
    if job_location is None:
        return None

    items = job_location if isinstance(job_location, list) else [job_location]
    for loc in items:
        if not isinstance(loc, dict):
            continue
        name = loc.get("name")
        if name:
            locations.append(name)
            continue
        address = loc.get("address")
        if isinstance(address, dict):
            parts = []
            for field in ("addressLocality", "addressRegion", "addressCountry"):
                val = address.get(field)
                if val:
                    if isinstance(val, dict):
                        val = val.get("name", "")
                    parts.append(str(val))
            if parts:
                locations.append(", ".join(parts))
    return locations or None


def _extract_salary(posting: dict) -> dict | None:
    base_salary = posting.get("baseSalary")
    if not isinstance(base_salary, dict):
        return None

    currency = base_salary.get("currency")
    value = base_salary.get("value")
    outer_unit = normalize_salary_unit(base_salary.get("unitText"))

    if isinstance(value, dict):
        inner_unit = normalize_salary_unit(value.get("unitText"))
        return {
            "currency": currency,
            "min": value.get("minValue"),
            "max": value.get("maxValue"),
            "unit": inner_unit or outer_unit,
        }
    elif isinstance(value, (int, float)):
        return {
            "currency": currency,
            "min": value,
            "max": value,
            "unit": outer_unit,
        }
    return None


def _text_or_list(val: Any) -> list[str] | None:
    if isinstance(val, str):
        return [val] if val.strip() else None
    if isinstance(val, list):
        result = [str(v).strip() for v in val if v]
        return result or None
    return None


def _parse_posting(posting: dict) -> ScrapedPostingPayload:
    extras: dict = {}
    skills = _text_or_list(posting.get("skills"))
    if skills:
        extras["skills"] = skills
    responsibilities = _text_or_list(posting.get("responsibilities"))
    if responsibilities:
        extras["responsibilities"] = responsibilities
    qualifications = _text_or_list(
        posting.get("qualifications") or posting.get("educationRequirements")
    )
    if qualifications:
        extras["qualifications"] = qualifications

    return ScrapedPostingPayload(
        title=posting.get("title") or posting.get("name"),
        description=posting.get("description"),
        locations=_extract_locations(posting),
        employment_type=posting.get("employmentType"),
        job_location_type=posting.get("jobLocationType"),
        date_posted=posting.get("datePosted"),
        base_salary=_extract_salary(posting),
        extras=extras or None,
    )


async def scrape(url: str, config: dict, http: httpx.AsyncClient) -> ScrapedPostingPayload | None:
    try:
        resp = await http.get(url, follow_redirects=True)
        if resp.status_code == 403:
            await asyncio.sleep(0.5 + random.random())
            resp = await http.get(url, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text
    except Exception:
        return None

    extractor = _JsonLdExtractor()
    extractor.feed(html)

    for block in extractor.results:
        posting = _find_job_posting(block)
        if posting:
            return _parse_posting(posting)
    return None


def can_handle(htmls: list[str]) -> dict | None:
    found = 0
    for html in htmls:
        extractor = _JsonLdExtractor()
        extractor.feed(html)
        if any(_find_job_posting(block) for block in extractor.results):
            found += 1
    if found > 0 and found >= len(htmls) / 2:
        return {}
    return None


register_scraper("json-ld", scrape, can_handle=can_handle)
