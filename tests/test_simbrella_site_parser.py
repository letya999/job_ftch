from __future__ import annotations

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.simbrella import SimbrellaParser


def test_simbrella_parser_extracts_ssr_vacancy_grid() -> None:
    html = """<div class="vacanciees_grid"><article><h3>IT / Software Engineer</h3>
    <a href="/all-vacancies/it-middle-level-software-engineer/" class="full_link"></a></article></div>"""

    items = SimbrellaParser._items_from_html(
        html, CareerSiteSpec(url="https://www.simbrella.com/all-vacancies/")
    )

    assert len(items) == 1
    assert (
        str(items[0].url)
        == "https://www.simbrella.com/all-vacancies/it-middle-level-software-engineer/"
    )
    assert "Software Engineer" in items[0].text
