from __future__ import annotations

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.blackrock import BlackRockParser


def test_blackrock_parser_ignores_non_job_links() -> None:
    html = """
    <li><a href="/job/london/full-stack-developer/45831/92643036192">Full Stack Developer</a></li><!-- pragma: allowlist secret -->
    <a href="/blog-career-growth">Career Growth</a>
    """
    items = BlackRockParser._items_from_html(
        html,
        CareerSiteSpec(url="https://www.blackrock.com/corporate/careers", source_name="BlackRock"),
    )

    assert len(items) == 1
    assert items[0].external_id == "92643036192"
    assert str(items[0].url).endswith("/45831/92643036192")
