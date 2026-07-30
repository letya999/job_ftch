from __future__ import annotations

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.solid_jobs import SolidJobsParser


def test_solid_jobs_parser_extracts_offer_cards() -> None:
    html = """
    <a class="offer" href="/offer/35385/renegades-programista-ai">
      Programista AI Renegades 100% zdalnie
    </a>
    <a href="/offers/it">IT offers</a>
    """
    items = SolidJobsParser._items_from_html(
        html,
        CareerSiteSpec(url="https://solid.jobs/", source_name="Solid.jobs"),
    )

    assert len(items) == 1
    assert items[0].external_id == "35385"
    assert str(items[0].url).endswith("/offer/35385/renegades-programista-ai")
