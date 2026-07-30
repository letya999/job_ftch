from __future__ import annotations

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.qyzmet import QyzmetParser


def test_qyzmet_parser_emits_inline_ssr_vacancy_card() -> None:
    html = """
    <article class="job" data-id="21103262">
      <h2><a class="job-title" href="/jobdesc?id=21103262&amp;src=js">Data Engineer</a></h2>
      <div class="job-data company">Example Corp</div>
      <div class="job-data region">Almaty</div>
      <div class="desc">Build reliable data products and pipelines.</div>
    </article>
    """
    spec = CareerSiteSpec(url="https://qyzmet.kz/вакансии", source_name="qyzmet-test")

    items = QyzmetParser._items_from_html(html, spec.url, spec)

    assert len(items) == 1
    assert items[0].external_id == "21103262"
    assert str(items[0].url) == "https://qyzmet.kz/jobdesc?id=21103262&src=js"
    assert items[0].metadata["company"] == "Example Corp"
