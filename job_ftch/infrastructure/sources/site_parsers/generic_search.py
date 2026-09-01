"""Search-form detection shared by source assessment and runtime.

Specific parser URL builders and this generic path are both tested by source
assessment; `supports_search` is only a hint and no longer bypasses the form
probe. For a generic career site we:

1. fetch the listing page and look for an HTML `<form>` with a text/search input
   (`detect_search_form`);
2. build a candidate combined query URL from it (`build_generic_search_url`);
3. probe whether the query actually filters the result set
   (`discover_working_search_url`) — comparing candidate-link counts for the
   combined query vs the unfiltered page vs a nonsense token — and only adopt a
   URL that demonstrably narrows the listing.

GET forms produce reproducible URLs; POST forms can produce a safe payload for
assessment/runtime. When nothing works the caller keeps the original listing
URL and the normal crawl proceeds unchanged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urljoin, urlparse, urlunparse

import structlog
from selectolax.parser import HTMLParser

from job_ftch.infrastructure.sources.site_parsers.helpers import (
    normalize_search_keywords,
    with_query_params,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = structlog.get_logger(__name__)

# Input name/placeholder hints that mark a keyword search box, most specific first.
_SEARCH_NAME_HINTS: tuple[str, ...] = (
    "search",
    "keywords",
    "keyword",
    "query",
    "text",
    "qs",
    "q",
    "s",
)
# Job-ish path tokens used to count "result" links on an arbitrary listing page.
_JOB_LINK_RE = re.compile(
    r"/(?:job|jobs|vacanc(?:y|ies)|position[s]?|opening[s]?|career[s]?|listing[s]?)/",
    re.IGNORECASE,
)
_NUMERIC_ID_RE = re.compile(r"/\d{3,}(?:[/?#]|$)")
_NONSENSE_TOKEN = "zzqxnonsense7391"


@dataclass(frozen=True)
class SearchFormSpec:
    """A discovered search form on a listing page."""

    action: str  # absolute submit URL
    method: str  # "get" | "post"
    query_param: str  # name of the free-text search input
    hidden: dict[str, str] = field(default_factory=dict)  # hidden fields to preserve

    @property
    def usable_via_url(self) -> bool:
        return self.method == "get"


def _input_search_score(node: Any) -> int:
    """Rank an <input> by how likely it is the keyword box (higher = better)."""
    attrs = node.attributes
    input_type = (attrs.get("type") or "text").strip().lower()
    if input_type in {"hidden", "checkbox", "radio", "submit", "button", "file"}:
        return -1
    name = (attrs.get("name") or "").strip().lower()
    if not name:
        return -1
    haystack = (
        f"{name} {(attrs.get('placeholder') or '').lower()} {(attrs.get('id') or '').lower()}"
    )
    score = 100 if input_type == "search" else 0
    for index, hint in enumerate(_SEARCH_NAME_HINTS):
        if name == hint or (len(hint) > 1 and haystack.find(hint) != -1):
            score += len(_SEARCH_NAME_HINTS) - index
            break
    return score


def detect_search_form(html: str, base_url: str) -> SearchFormSpec | None:
    """Return the best keyword-search form on the page, or None if there is none.

    GET forms are preferred over POST forms; within a method, the form whose
    input scores highest on search-name heuristics wins.
    """
    tree = HTMLParser(html or "")
    best: SearchFormSpec | None = None
    best_rank: tuple[int, int] = (-1, -1)
    for form in tree.css("form"):
        method = (form.attributes.get("method") or "get").strip().lower()
        method = "post" if method == "post" else "get"
        candidate_input: Any = None
        candidate_score = -1
        for node in form.css("input"):
            score = _input_search_score(node)
            if score > candidate_score:
                candidate_input = node
                candidate_score = score
        if candidate_input is None or candidate_score <= 0:
            continue
        query_param = (candidate_input.attributes.get("name") or "").strip()
        if not query_param:
            continue
        hidden = {
            (node.attributes.get("name") or "").strip(): (node.attributes.get("value") or "")
            for node in form.css("input")
            if (node.attributes.get("type") or "").strip().lower() == "hidden"
            and (node.attributes.get("name") or "").strip()
        }
        action = urljoin(base_url, (form.attributes.get("action") or "").strip() or base_url)
        # GET beats POST; then higher input score. Encode as a comparable rank.
        rank = (1 if method == "get" else 0, candidate_score)
        if rank > best_rank:
            best_rank = rank
            best = SearchFormSpec(
                action=action, method=method, query_param=query_param, hidden=hidden
            )
    # Some server-rendered boards expose a search contract only through
    # schema.org SearchAction, without rendering an HTML form.  Treat its
    # URL template as a reproducible GET form so assessment and runtime use the
    # same path.  Ignore malformed or non-search templates.
    tree = HTMLParser(html or "")
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            payload = json.loads(node.text())
        except (TypeError, json.JSONDecodeError):
            continue
        actions = payload if isinstance(payload, list) else [payload]
        for entry in actions:
            if not isinstance(entry, dict):
                continue
            potential_action = entry.get("potentialAction")
            candidates = (
                potential_action if isinstance(potential_action, list) else [potential_action]
            )
            for candidate in candidates:
                if not isinstance(candidate, dict) or candidate.get("@type") != "SearchAction":
                    continue
                target = candidate.get("target")
                template = target.get("urlTemplate") if isinstance(target, dict) else target
                if not isinstance(template, str) or "{search_term_string}" not in template:
                    continue
                absolute = urljoin(base_url, template.replace("{search_term_string}", ""))
                parsed = urlparse(absolute)
                params = dict(parse_qsl(parsed.query, keep_blank_values=True))
                query_param = next(
                    (
                        key
                        for key, value in parse_qsl(
                            urlparse(template).query, keep_blank_values=True
                        )
                        if "search_term_string" in value
                    ),
                    None,
                )
                if query_param is None:
                    continue
                return SearchFormSpec(
                    action=urlunparse(parsed._replace(query="")),
                    method="get",
                    query_param=query_param,
                    hidden={key: value for key, value in params.items() if key != query_param},
                )
    return best


def build_generic_search_url(form: SearchFormSpec, query: str) -> str | None:
    """Build a GET search URL from a form + query string, or None if not possible."""
    if not form.usable_via_url or not query.strip():
        return None
    params = {**form.hidden, form.query_param: query}
    return with_query_params(form.action, params)


def build_generic_search_payload(form: SearchFormSpec, query: str) -> dict[str, str] | None:
    """Build a safe form payload for a POST search probe or browser submit."""
    if not query.strip():
        return None
    return {**form.hidden, form.query_param: query}


def count_candidate_job_links(html: str, base_url: str) -> int:
    """Count distinct same-host anchors that look like job/detail links."""
    tree = HTMLParser(html or "")
    host = (urlparse(base_url).hostname or "").lower()
    seen: set[str] = set()
    for anchor in tree.css("a[href]"):
        href = (anchor.attributes.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if host and (parsed.hostname or "").lower() not in {
            host,
            f"www.{host}",
            host.removeprefix("www."),
        }:
            continue
        path = parsed.path
        if _JOB_LINK_RE.search(path) or _NUMERIC_ID_RE.search(path):
            seen.add(absolute.split("#", 1)[0])
    return len(seen)


async def discover_working_search_url(
    fetch: Any,
    base_url: str,
    keywords: Sequence[str],
    *,
    log: Any = None,
) -> str | None:
    """Probe a site's search form and return a working combined-query URL, or None.

    `fetch` is an async callable `fetch(url) -> str` returning page HTML (the
    caller supplies one bound to its retry/bypass-aware client). The result is a
    single combined-query URL that measurably narrows the listing; per-keyword
    fan-out is intentionally out of scope for the generic runtime path.
    """
    log = log or logger
    terms = normalize_search_keywords(keywords)
    if not terms:
        return None
    try:
        base_html = await fetch(base_url)
    except Exception as exc:  # noqa: BLE001 - detection is best-effort
        log.debug("generic_search_base_fetch_failed", url=base_url, error=str(exc))
        return None

    form = detect_search_form(base_html, base_url)
    if form is None:
        log.debug("generic_search_no_form", url=base_url)
        return None
    if not form.usable_via_url:
        log.info("generic_search_post_only_form", url=base_url, param=form.query_param)
        return None

    unfiltered = count_candidate_job_links(base_html, base_url)
    nonsense_url = build_generic_search_url(form, _NONSENSE_TOKEN)
    # Unknown sites use different boolean conventions; try both operators.
    candidates = [
        build_generic_search_url(form, " OR ".join(terms)),
        build_generic_search_url(form, " or ".join(terms)),
    ]
    candidates.extend(build_generic_search_url(form, term) for term in terms[:3])

    async def _count(url: str | None) -> int:
        if not url:
            return 0
        try:
            return count_candidate_job_links(await fetch(url), base_url)
        except Exception as exc:  # noqa: BLE001
            log.debug("generic_search_probe_fetch_failed", url=url, error=str(exc))
            return -1

    nonsense_count = await _count(nonsense_url)
    if nonsense_count >= 0 and unfiltered >= 3 and nonsense_count >= max(1, int(0.8 * unfiltered)):
        # The query parameter is ignored: nonsense returns ~the full listing.
        log.info(
            "generic_search_query_ignored",
            url=base_url,
            unfiltered=unfiltered,
            nonsense_results=nonsense_count,
            reason="nonsense_not_narrowed",
        )
        return None

    best_url: str | None = None
    best_count = 0
    for candidate in candidates:
        count = await _count(candidate)
        # A working query yields at least one result and does not simply return
        # the whole (or larger) unfiltered page.
        if not candidate or candidate == base_url:
            continue
        if count > best_count and (unfiltered == 0 or count < unfiltered):
            best_count = count
            best_url = candidate
    if best_url and best_count > 0:
        log.info(
            "generic_search_url_adopted",
            url=base_url,
            search_url=best_url,
            results=best_count,
            unfiltered=unfiltered,
        )
        return best_url
    log.debug("generic_search_no_working_query", url=base_url, unfiltered=unfiltered)
    return None
