from __future__ import annotations

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.spitamen import SpitamenParser


def test_extracts_inline_spitamen_vacancy() -> None:
    html = """
    <section class="sb-item" id="vacancy-17">
      <h3 class="sb-title">Data analyst</h3>
      <div class="sb-content">Analyze customer and financial data for the bank.</div>
    </section>
    """
    spec = CareerSiteSpec(
        url="https://spitamenbank.tj/tj/vacancies/",
        source_name="Spitamenbank",
    )

    items = SpitamenParser._items_from_html(html, spec)

    assert len(items) == 1
    assert items[0].external_id == "vacancy-17"
    assert str(items[0].url) == "https://spitamenbank.tj/tj/vacancies#vacancy-17"
    assert "Data analyst" in items[0].text
