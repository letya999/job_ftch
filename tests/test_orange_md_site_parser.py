from __future__ import annotations

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.orange_md import OrangeMdParser


def test_extracts_ecruiter_inline_vacancy_row() -> None:
    html = """
    <table><tbody>
      <tr skkresult="offer" jobofferid="3494677">
        <td class="skk_positionName">DATA Analyst</td>
        <td>Chisinau</td><td>MD_IT</td>
      </tr>
    </tbody></table>
    """
    spec = CareerSiteSpec(url="https://www.orange.md/ro/cariere", source_name="Orange Mnewova")

    items = OrangeMdParser._items_from_html(html, spec)

    assert len(items) == 1
    assert items[0].external_id == "3494677"
    assert str(items[0].url).endswith("#job-3494677")
    assert items[0].metadata["location"] == "Chisinau"
