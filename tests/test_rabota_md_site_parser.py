from __future__ import annotations

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.rabota_md import RabotaMdParser


def test_rabota_md_parser_emits_ssr_vacancy_card() -> None:
    html = """
    <div class="vacancyCardItem previewCard" data-vacancyid="141340">
      <a href="/ro/locuri-de-munca/middle-senior-net-developer/141340">.NET Developer</a>
      <p>Build and maintain reliable business applications.</p>
    </div>
    """
    spec = CareerSiteSpec(url="https://www.rabota.md/ro/all", source_name="rabota-test")

    items = RabotaMdParser._items_from_html(html, "https://www.rabota.md/ro/jobs-mnewova", spec)

    assert len(items) == 1
    assert items[0].external_id == "141340"
    assert (
        str(items[0].url)
        == "https://www.rabota.md/ro/locuri-de-munca/middle-senior-net-developer/141340"
    )
