from __future__ import annotations

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.askwire import AskWireParser


def test_extracts_inline_ask_wire_role_with_stable_identity() -> None:
    html = """
    <div class="wp-block-kadence-tabs">
      <span class="kt-title-text"><strong>Senior Data Scientist</strong></span>
      <span class="kt-title-sub-text">Athens, Greece – Hybrid</span>
      <div class="kt-tab-inner-content">Build resilient data pipelines and ML models for clients.</div>
    </div>
    """
    spec = CareerSiteSpec(url="https://ask-wire.com/careers/", source_name="Ask Wire")

    first = AskWireParser._items_from_html(html, spec)
    second = AskWireParser._items_from_html(html, spec)

    assert len(first) == 1
    assert first[0].external_id == second[0].external_id
    assert str(first[0].url).endswith(f"#role-{first[0].external_id}")
    assert "Athens" in first[0].text
