from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from job_ftch.application.registry import resolve_site_parser
from job_ftch.domain.site_models import ScrapedPostingPayload
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.career_site_source import _looks_like_consent_interstitial
from job_ftch.infrastructure.sources.site_parsers.wantapply import (
    WantapplyParser,
    _detail_urls,
)

_FIXTURES = Path(__file__).parent / "fixtures/source_pages"


def test_consent_fixture_is_not_vacancy_content() -> None:
    html = (_FIXTURES / "jseek_consent.html").read_text(encoding="utf-8")
    assert _looks_like_consent_interstitial(
        ScrapedPostingPayload(title="Cookie preferences", description=html)
    )
    assert not _looks_like_consent_interstitial(
        ScrapedPostingPayload(
            title="AI Engineer",
            description="We use cookies. Responsibilities and requirements for an AI engineer. "
            * 30,
        )
    )


def test_wantapply_listing_fixture_keeps_only_detail_urls() -> None:
    html = (_FIXTURES / "wantapply_listing.html").read_text(encoding="utf-8")
    assert _detail_urls(html, "https://wantapply.com/jobs/ai-engineer-prompt", 10) == [
        "https://wantapply.com/ai-engineer-at-phantom"
    ]
    assert isinstance(resolve_site_parser("https://wantapply.com/"), WantapplyParser)


@pytest.mark.asyncio
async def test_wantapply_uses_tls_fallback_and_extracts_detail_fixture(monkeypatch: Any) -> None:
    listing = (_FIXTURES / "wantapply_listing.html").read_text(encoding="utf-8")
    detail = (_FIXTURES / "wantapply_detail.html").read_text(encoding="utf-8")

    class _Response:
        def __init__(self, status_code: int, text: str) -> None:
            self.status_code = status_code
            self.text = text

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

    class _BlockedClient:
        async def get(self, url: str, **kwargs: Any) -> _Response:
            del url, kwargs
            return _Response(403, "blocked")

    class _TLSClient:
        async def __aenter__(self) -> _TLSClient:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def get(self, url: str, **kwargs: Any) -> _Response:
            del kwargs
            return _Response(200, detail if "-at-" in url else listing)

    monkeypatch.setattr("curl_cffi.requests.AsyncSession", lambda **kwargs: _TLSClient())
    spec = CareerSiteSpec(url="https://wantapply.com/jobs/ai-engineer-prompt", limit=1)
    jobs = [item async for item in WantapplyParser().parse(spec, _BlockedClient())]
    assert len(jobs) == 1
    assert jobs[0].metadata["title"] == "AI Engineer"
    assert "model evaluation" in jobs[0].text
    assert str(jobs[0].url) == "https://wantapply.com/ai-engineer-at-phantom"
