from __future__ import annotations

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.pointer import PointerParser


def test_extracts_public_pointer_card_with_stable_synthetic_identity() -> None:
    html = """
    <app-vacany>
      <article class="card">
        <h3 class="card-title">Platform Engineer</h3>
        <span class="pin-block">Tbilisi</span>
      </article>
    </app-vacany>
    """
    spec = CareerSiteSpec(
        url="https://jobs.pointer.ge/basisbank/vacancies",
        source_name="Basisbank Pointer",
    )

    first = PointerParser._items_from_html(html, spec)
    second = PointerParser._items_from_html(html, spec)

    assert len(first) == 1
    assert first[0].external_id == second[0].external_id
    assert str(first[0].url).endswith(f"#vacancy-{first[0].external_id}")
    assert "Platform Engineer" in first[0].text
