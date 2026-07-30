from job_ftch.infrastructure.bypass.humanize import RequestHumanizer


def test_detail_page_gets_listing_referer():
    h = RequestHumanizer()
    h.set_listing_url("example.com", "https://example.com/jobs")
    referer = h.get_referer("https://example.com/jobs/123")
    assert referer == "https://example.com/jobs"


def test_first_visit_without_listing_gets_search_referer():
    h = RequestHumanizer()
    referer = h.get_referer("https://other.com/jobs/456")
    assert referer.startswith("https://www.google.com") or referer.startswith(
        "https://duckduckgo.com"
    )


def test_listing_url_itself_gets_search_referer():
    h = RequestHumanizer()
    h.set_listing_url("example.com", "https://example.com/jobs")
    referer = h.get_referer("https://example.com/jobs")
    assert referer.startswith("https://www.google.com") or referer.startswith(
        "https://duckduckgo.com"
    )
