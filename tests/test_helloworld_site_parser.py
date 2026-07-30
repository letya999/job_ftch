from __future__ import annotations

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.helloworld import HelloWorldParser


def test_extracts_helloworld_detail_link_and_card_context() -> None:
    html = """
    <article><a href="/posao/cloud-developer-ai-integrations/robert-bosch-doo/744664?source=home">
      Cloud Developer (AI Integrations)</a><span>Beograd | Hybrid</span></article>
    """
    spec = CareerSiteSpec(url="https://www.helloworld.rs/", source_name="HelloWorld.rs")

    items = HelloWorldParser._items_from_html(html, spec)

    assert len(items) == 1
    assert items[0].external_id == "744664"
    assert str(items[0].url).endswith("/744664")
    assert "Cloud Developer" in items[0].text
