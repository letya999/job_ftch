from __future__ import annotations

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.bolt import BoltParser


def test_extracts_ssr_bolt_accordion_role() -> None:
    html = """
    <div data-testid="AccordionItem">
      <h3><span>Account Manager</span></h3>
      <span>Sales &amp; Account Management</span><span>Tallinn</span>
      <p>Help grow Bolt's platform across Europe and Africa.</p>
      <a href="/en/careers/positions/4171afc8-2e65-45fe-8e7a-691626d84ce1/">View role</a>
    </div>
    """
    spec = CareerSiteSpec(url="https://bolt.eu/en/careers/", source_name="Bolt")

    items = BoltParser._items_from_html(html, spec)

    assert len(items) == 1
    assert items[0].external_id == "4171afc8-2e65-45fe-8e7a-691626d84ce1"
    assert str(items[0].url).endswith("4171afc8-2e65-45fe-8e7a-691626d84ce1/")
    assert "Account Manager" in items[0].text
