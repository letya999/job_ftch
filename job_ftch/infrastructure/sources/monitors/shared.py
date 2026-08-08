from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from job_ftch.domain.site_models import MonitorResult

if TYPE_CHECKING:
    import httpx

MAX_JOBS = 50_000
_UNAVAILABLE_LISTING_PLATFORM_RE = re.compile(
    r"\bplatforma\s+nu\s+este\s+disponibil[ăa]\b.{0,500}"
    r"\b(?:anunțurile|anunturile)\b",
    re.IGNORECASE | re.DOTALL,
)


class BoardGoneError(Exception):
    """The upstream ATS confirms the board no longer exists."""

    def __init__(self, message: str, *, url: str | None = None) -> None:
        super().__init__(message)
        self.url = url


class AtsRedirectException(Exception):
    """Raised when a generic monitor discovers an embedded/linked ATS board."""

    def __init__(self, monitor_name: str, url: str) -> None:
        super().__init__(f"Redirect to ATS: {monitor_name} at {url}")
        self.monitor_name = monitor_name
        self.url = url


class BrowserChallengeError(Exception):
    """The browser loaded an anti-bot challenge instead of the requested board."""

    def __init__(
        self,
        *,
        url: str,
        status_code: int | None = None,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        challenge_type: str | None = None,
        confidence: float | None = None,
        evidence_hash: str | None = None,
    ) -> None:
        super().__init__(f"blocked browser challenge at {url}")
        self.url = url
        self.status_code = status_code
        self.headers = headers or {}
        self.body = body
        self.challenge_type = challenge_type
        self.confidence = confidence
        self.evidence_hash = evidence_hash


def raise_if_browser_challenge(html: str, *, url: str) -> None:
    """Reject a rendered CAPTCHA page before it is treated as a job listing."""
    from urllib.parse import urlparse

    from job_ftch.infrastructure.bypass.challenge_classifier import (
        classify_challenge,
        emit_challenge_detection,
    )

    body = html.encode("utf-8", errors="ignore")
    detection = classify_challenge(
        surface="monitor",
        status_code=200,
        body=body,
    )
    if detection.detected:
        emit_challenge_detection(urlparse(url).netloc.lower(), detection)
        raise BrowserChallengeError(
            url=url,
            status_code=200,
            body=body,
            challenge_type=detection.challenge_type,
            confidence=detection.confidence,
            evidence_hash=detection.evidence_hash,
        )


def slugs_from_url(url: str) -> list[str]:
    """Derive candidate ATS board slugs from a URL."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    parts = host.split(".")
    if len(parts) >= 2:
        return [parts[-2]]
    return [parts[0]] if parts else []


def is_soft_404(html: str) -> bool:
    """Detect if the page is a soft 404."""
    if not html:
        return False
    lower_html = html.lower()

    # Check title
    import re

    title_match = re.search(r"<title>(.*?)</title>", lower_html)
    if title_match:
        title = title_match.group(1).strip()
        if (
            "404" in title
            or "not found" in title
            or "страница не найдена" in title
            or "page not found" in title
        ):
            return True

    # Check the primary visible heading. Some modern sites put their 404 copy
    # in an h2 while reserving h1 for layout or omit h1 entirely.
    heading_match = re.search(r"<h[12][^>]*>(.*?)</h[12]>", lower_html)
    if heading_match:
        heading = heading_match.group(1).strip()
        if (
            "404" in heading
            or "not found" in heading
            or "страница не найдена" in heading
            or "page not found" in heading
            or "запрашиваемая страница не найдена" in heading
        ):
            return True

    return False


def is_board_gone(html: str) -> bool:
    """Recognise an explicit unavailable listing platform in fetched HTML."""
    return bool(html and _UNAVAILABLE_LISTING_PLATFORM_RE.search(html))


def check_ats_redirect(html: str, board_url: str) -> None:
    """Check if the rendered HTML contains a known ATS link, and raise AtsRedirectException if found."""
    if not html:
        return

    from job_ftch.infrastructure.sources.site_fingerprinter import _specific_monitor_from_text

    ats_name = _specific_monitor_from_text(board_url, html)
    if ats_name:
        import re

        from job_ftch.application.registry import get_all_monitor_entries

        entries = get_all_monitor_entries()
        entry = next((e for e in entries if e.name == ats_name), None)
        if entry and entry.assessment_hint:
            for pattern in entry.assessment_hint.url_patterns:
                match = re.search(
                    r'["\'](https?://[^"\']*?' + pattern + r'[^"\']*?)["\']', html, re.IGNORECASE
                )
                if match:
                    raise AtsRedirectException(ats_name, match.group(1))


async def fetch_page_text(
    url: str,
    client: httpx.AsyncClient,
    max_chars: int | None = None,
) -> str | None:
    """Fetch a page and return its text content (capped). Raises exceptions on network or HTTP errors."""
    if max_chars is None:
        from job_ftch.config import get_settings

        max_chars = get_settings().monitor_page_text_max_chars
    resp = await client.get(url, follow_redirects=True)
    resp.raise_for_status()
    # Preserve invalid bytes in a misdeclared document as surrogate escapes.
    # The DOM monitor can then repair legacy href encodings instead of losing
    # their original bytes through the normal replacement-character decoder.
    raw_content = getattr(resp, "content", None)
    if isinstance(raw_content, bytes):
        encoding = getattr(resp, "encoding", None) or "utf-8"
        text = raw_content.decode(encoding, errors="surrogateescape")[:max_chars]
    else:
        text = resp.text[:max_chars]
    if is_soft_404(text):
        raise BoardGoneError("Soft 404 detected", url=url)
    if is_board_gone(text):
        raise BoardGoneError("Listing platform unavailable", url=url)
    return text


def truncated_rich_result(payloads: list[Any]) -> MonitorResult:
    """Cap a rich result and set the truncated flag."""
    capped = payloads[:MAX_JOBS]
    return MonitorResult(
        urls={p.url for p in capped},
        payloads_by_url={p.url: p for p in capped},
        truncated=len(payloads) > MAX_JOBS,
    )


def truncated_url_result(urls: set[str]) -> MonitorResult:
    """Cap a URL-only result and set the truncated flag."""
    capped = sorted(list(urls))[:MAX_JOBS]
    return MonitorResult(
        urls=set(capped),
        truncated=len(urls) > MAX_JOBS,
    )


def normalize_job_location_type(value: str, default: str | None = "onsite") -> str | None:
    """Map string location types to domain enums (remote, hybrid, onsite)."""
    if not value:
        return default
    v = value.lower()
    if "remote" in v:
        return "remote"
    if "hybrid" in v:
        return "hybrid"
    if "onsite" in v or "on-site" in v or "office" in v:
        return "onsite"
    return default


def normalize_salary_unit(unit: str | None) -> str | None:
    """Normalize salary interval tokens."""
    if not unit:
        return None
    unit = unit.lower().strip()
    if unit in ("year", "yearly", "annually", "annual"):
        return "year"
    if unit in ("month", "monthly"):
        return "month"
    if unit in ("hour", "hourly"):
        return "hour"
    return unit
