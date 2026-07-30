from __future__ import annotations

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.kapital_bir import KapitalBirParser


def test_kapital_bir_parser_emits_rendered_vacancy_card() -> None:
    html = """
    <a href="/vacancies/7423"><h3>Data Analyst</h3>
      <div>Build reliable analytical products for our clients.</div>
    </a>
    """
    spec = CareerSiteSpec(url="https://careers.bir.az/vacancies", source_name="kapital-test")

    items = KapitalBirParser._items_from_html(html, spec.url, spec)

    assert len(items) == 1
    assert items[0].external_id == "7423"
    assert str(items[0].url) == "https://careers.bir.az/vacancies/7423"
