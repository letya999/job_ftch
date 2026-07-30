from __future__ import annotations

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.softserve import SoftServeParser


def test_softserve_parser_extracts_vacancy_detail_links() -> None:
    html = """
    <a href="/en-us/vacancies/lead-big-data-engineer-89232">Lead Big Data Engineer Poland</a>
    <a href="/en-us/vacancies">All jobs</a>
    """
    items = SoftServeParser._items_from_html(
        html,
        CareerSiteSpec(
            url="https://career.softserveinc.com/en-us/vacancies/country-poland",
            source_name="SoftServe",
        ),
    )

    assert len(items) == 1
    assert items[0].external_id == "89232"
    assert str(items[0].url).endswith("lead-big-data-engineer-89232")
