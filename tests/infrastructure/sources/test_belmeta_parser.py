from __future__ import annotations

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.belmeta import BelmetaParser


def test_belmeta_parser_emits_each_inline_job_card() -> None:
    html = """
    <article data-id="15307015" class="job no-logo">
      <h2 class="title"><a href="/jobdesc?id=15307015&amp;src=js" class="job-title">ML Engineer</a></h2>
      <div class="company-job-data">
        <div class="job-data company">Example Corp</div>
        <div class="job-data region">Minsk</div>
      </div>
      <div class="desc">Build reliable machine-learning systems.</div>
    </article>
    <article data-id="15307016" class="job">
      <h2 class="title"><a href="/jobdesc?id=15307016" class="job-title">Data Analyst</a></h2>
      <div class="desc">Analyze product data and customer behaviour.</div>
    </article>
    """
    spec = CareerSiteSpec(url="https://belmeta.com/", source_name="belmeta-test")

    items = BelmetaParser._items_from_html(html, "https://belmeta.com/vacancies/acme", spec)

    assert [item.external_id for item in items] == ["15307015", "15307016"]
    assert items[0].metadata["title"] == "ML Engineer"
    assert items[0].metadata["location"] == "Minsk"
    assert str(items[0].url) == "https://belmeta.com/jobdesc?id=15307015&src=js"
    assert "machine-learning" in items[0].text
