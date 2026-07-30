from __future__ import annotations

from job_ftch.infrastructure.sources.site_parsers.hireme import _extract_listing_detail_urls


def test_extract_listing_detail_urls_keeps_only_hireme_job_cards() -> None:
    html = """
    <a href="/pwc-data-analytics/">PwC Data &amp; Analytics</a>
    <a class="mpp-title-link mp-post-title" href="/ai-engineer/">AI Engineer</a>
    <a class="mp-post-btn" href="/ai-engineer/">Details</a>
    """

    assert _extract_listing_detail_urls(html, "https://hireme.kz/", limit=5) == [
        "https://hireme.kz/ai-engineer/"
    ]
