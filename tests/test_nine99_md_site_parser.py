from __future__ import annotations

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.nine99_md import Nine99MdParser


def test_extracts_hydrated_999_md_work_card() -> None:
    html = """
    <div data-testid="infinite-ads-home">
      <a href="/ro/84329860?location=infinite_home_work">
        <h4>Casier</h4><p>MULTIMOBILE</p><p>Chișinău</p>
        <p>Casier Magazin Multibrand cu salariu motivant.</p>
      </a>
    </div>
    """
    spec = CareerSiteSpec(url="https://999.md/", source_name="999.md Jobs")

    items = Nine99MdParser._items_from_html(html, spec)

    assert len(items) == 1
    assert items[0].external_id == "84329860"
    assert str(items[0].url) == "https://999.md/ro/84329860"
    assert "MULTIMOBILE" in items[0].text
