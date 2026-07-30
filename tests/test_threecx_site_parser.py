from __future__ import annotations

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.threecx import ThreeCxParser


def test_extracts_odoo_iframe_vacancy_card() -> None:
    html = """
    <div id="jobs_grid"><a href="/jobs/accountant-nicosia-cyprus-9">
      <h3>Accountant (Nicosia, Cyprus)</h3><span>1 open position</span>
      <span>Nicosia, Cyprus</span><span>Finance</span>
    </a></div>
    """
    spec = CareerSiteSpec(url="https://www.3cx.com/company/jobs/", source_name="3CX")

    items = ThreeCxParser._items_from_html(html, "https://ops.3cx.com/jobs", spec)

    assert len(items) == 1
    assert items[0].external_id == "accountant-nicosia-cyprus-9"
    assert str(items[0].url) == "https://ops.3cx.com/jobs/accountant-nicosia-cyprus-9"
