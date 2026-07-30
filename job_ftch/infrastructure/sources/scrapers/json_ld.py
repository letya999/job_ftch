"""JSON-LD scraper ported from jobseek.

Primary path: stdlib HTMLParser extracts application/ld+json blocks and looks
for a JobPosting schema object. Fast and robust against broken JSON inside
<script> tags via _escape_control_chars_in_strings.

Secondary path (extruct fallback): when the stdlib path finds no JobPosting,
extruct scans the page for Microdata and OpenGraph structured data. Requires
the [extraction] extra. Skipped silently when the extra is absent.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any, cast

from job_ftch.application.registry import register_scraper
from job_ftch.domain.site_models import ScrapedPostingPayload
from job_ftch.infrastructure.sources.http_retry import fetch_with_retry
from job_ftch.infrastructure.sources.monitors.shared import normalize_salary_unit

if TYPE_CHECKING:
    import httpx

import structlog

logger = structlog.get_logger(__name__)


_ARTICLE_OPEN_GRAPH_RE = re.compile(
    r"<meta\b[^>]+(?:property|name)=[\"']og:type[\"'][^>]+content=[\"']article[\"']"
    r"|<meta\b[^>]+content=[\"']article[\"'][^>]+(?:property|name)=[\"']og:type[\"']",
    re.IGNORECASE,
)


def _declares_article(html: str) -> bool:
    """Return whether metadata explicitly identifies this document as an article.

    OpenGraph title/description is useful only as an unstructured last resort.
    It must not turn a blog post into a vacancy merely because it has rich SEO
    metadata. Structured ``JobPosting`` is handled before this fallback.
    """
    return bool(_ARTICLE_OPEN_GRAPH_RE.search(html))


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
        self.results: list[dict[str, Any]] = []

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


def _find_job_posting(data: dict[str, Any] | list[Any]) -> dict[str, Any] | None:
    if isinstance(data, list):
        for item in data:
            result = _find_job_posting(item)
            if result:
                return result
        return None

    if isinstance(data, dict):
        type_val = data.get("@type", "")
        if isinstance(type_val, str) and "JobPosting" in type_val:
            return cast("dict[str, Any]", _normalize_keys(data))
        if isinstance(type_val, list) and any("JobPosting" in t for t in type_val):
            return cast("dict[str, Any]", _normalize_keys(data))

        graph = data.get("@graph")
        if isinstance(graph, list):
            return _find_job_posting(graph)

    return None


def _extract_locations(posting: dict[str, Any]) -> list[str] | None:
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


def _extract_salary(posting: dict[str, Any]) -> dict[str, Any] | None:
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


def _parse_posting(posting: dict[str, Any]) -> ScrapedPostingPayload:
    extras: dict[str, Any] = {}
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


def parse_html(html: str, *, url: str = "") -> ScrapedPostingPayload | None:
    """Extract a JobPosting from HTML.

    Tries the stdlib JSON-LD fast path first. Falls back to extruct Microdata
    and OpenGraph when JSON-LD finds nothing. The extruct path requires the
    [extraction] extra and is silently skipped when absent.
    """
    extractor = _JsonLdExtractor()
    extractor.feed(html)

    for block in extractor.results:
        posting = _find_job_posting(block)
        if posting:
            return _parse_posting(posting)

    return _extruct_fallback(html, url)


def _extruct_fallback(html: str, url: str) -> ScrapedPostingPayload | None:
    """Try Microdata / OpenGraph via extruct when JSON-LD finds no JobPosting.

    Requires the [extraction] extra (extruct>=0.16). Returns None when the
    extra is absent or no structured JobPosting data is found.
    """
    try:
        import extruct
    except ImportError:
        return None

    try:
        data = extruct.extract(
            html,
            base_url=url,
            syntaxes=["microdata", "opengraph"],
            uniform=True,
        )
    except Exception as exc:
        logger.debug("jsonld.extruct_error", url=url, error=str(exc))
        return None

    # Microdata: look for a JobPosting item type
    for item in data.get("microdata", []):
        type_val = item.get("@type", "")
        if isinstance(type_val, str) and "JobPosting" in type_val:
            # ``extruct(..., uniform=True)`` returns flattened properties in
            # current releases, while newer releases nest them under
            # ``properties``. Support both representations.
            props = item.get("properties")
            if not isinstance(props, dict):
                props = {key: value for key, value in item.items() if not key.startswith("@")}
            normalized = _normalize_keys({"@type": type_val, **props})
            payload = _parse_posting(normalized)
            if payload.title or payload.description:
                logger.debug("jsonld.extruct_microdata_hit", url=url)
                return payload

    # OpenGraph is a very weak signal.  An explicit article type is stronger
    # evidence that this is editorial content, so never emit it as a vacancy.
    if _declares_article(html):
        return None

    og = data.get("opengraph", [])
    og_map: dict[str, Any] = {}
    for block in og if isinstance(og, list) else [og]:
        og_map.update(block)
    title = og_map.get("og:title") or og_map.get("title")
    description = og_map.get("og:description") or og_map.get("description")
    if title and description and len(str(description)) >= 120:
        logger.debug("jsonld.extruct_opengraph_hit", url=url)
        return ScrapedPostingPayload(title=str(title), description=str(description))

    return None


async def _fetch_html(url: str, http: httpx.AsyncClient) -> str:
    """GET the page via the shared retry helper."""
    resp = await fetch_with_retry(http, url)
    # A single cold-session 401/403 retry can accept a clearance cookie set by
    # the first response. More retries would hide a real challenge from the
    # adaptive route policy and are intentionally forbidden.
    if resp.status_code in {401, 403}:
        resp = await fetch_with_retry(http, url, max_attempts=1)
    resp.raise_for_status()
    return str(resp.text)


async def scrape(
    url: str, config: dict[str, Any], http: httpx.AsyncClient
) -> ScrapedPostingPayload | None:
    try:
        html = config.get("prefetched_html")
        if not isinstance(html, str):
            html = await _fetch_html(url, http)
    except Exception as exc:
        logger.warning("jsonld.scrape.fetch_failed", url=url, error=str(exc))
        return None

    return parse_html(html, url=url)


def can_handle(htmls: list[str]) -> dict[str, Any] | None:
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
