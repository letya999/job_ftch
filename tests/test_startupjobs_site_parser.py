from __future__ import annotations

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.startupjobs import StartupJobsParser


def test_startupjobs_parser_extracts_only_job_detail_links() -> None:
    html = """
    <section>
      <a href="/ai-engineer-sigma-group-8762500">AI Engineer</a>
      <span>SiGMA Group · Serbia</span>
    </section>
    <a href="https://startup.jobs/company/sigma-group">SiGMA Group</a>
    <a href="https://startup.jobs/locations/serbia">Serbia</a>
    <a href="https://startup.jobs/ai-engineer-sigma-group-8762500?source=apply">Apply</a>
    """

    items = StartupJobsParser._items_from_html(
        html,
        CareerSiteSpec(url="https://startup.jobs/locations/serbia", source_name="Startup Serbia"),
    )

    assert len(items) == 1
    assert items[0].external_id == "8762500"
    assert str(items[0].url) == "https://startup.jobs/ai-engineer-sigma-group-8762500"
    assert "AI Engineer" in items[0].text
