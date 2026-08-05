from __future__ import annotations

from pathlib import Path

from paritylab.catalog import load_catalog
from paritylab.catalog.audit import discover_static_finding_codes, undocumented_codes
from paritylab.catalog.schema import CoverageStatus


def test_catalog_is_valid_and_has_sota_breadth() -> None:
    catalog = load_catalog()
    assert catalog.validate() == ()
    assert len(catalog.surfaces) >= 35
    assert len(catalog.mechanics) >= 25
    assert len(catalog.countermeasures) >= 10
    families = {item.family for item in catalog.surfaces}
    assert {
        "network",
        "transport",
        "runtime",
        "rendering",
        "media",
        "integrity",
        "realm",
        "behavior",
        "session",
    } <= families


def test_catalog_does_not_claim_planned_surfaces_are_implemented() -> None:
    catalog = load_catalog()
    by_id = {item.id: item for item in catalog.surfaces}
    assert by_id["rendering.webgpu"].status is CoverageStatus.IMPLEMENTED
    assert by_id["network.http2.frames"].status is CoverageStatus.IMPLEMENTED
    assert by_id["network.http3.frames"].status is CoverageStatus.IMPLEMENTED
    for surface_id in (
        "behavior.pointer",
        "behavior.keyboard",
        "behavior.scroll",
        "behavior.touch",
    ):
        assert by_id[surface_id].status is CoverageStatus.IMPLEMENTED


def test_catalog_links_findings_to_mechanics_and_countermeasures() -> None:
    catalog = load_catalog()
    assert all(item.detects for item in catalog.findings)
    assert all(item.countermeasures for item in catalog.findings)
    assert all(item.surfaces for item in catalog.mechanics)
    assert all(item.countermeasures for item in catalog.mechanics)


def test_every_static_scorer_finding_is_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    codes = discover_static_finding_codes(root / "paritylab" / "scoring")
    assert undocumented_codes(load_catalog(), codes) == ()
