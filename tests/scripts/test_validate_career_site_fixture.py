from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_validator():
    path = Path(__file__).parents[2] / "scripts" / "validate_career_site_fixture.py"
    spec = importlib.util.spec_from_file_location("validate_career_site_fixture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_world_fixture_is_valid() -> None:
    module = _load_validator()
    path = Path(__file__).parents[2] / "fixtures" / "sources" / "career_sites_cis_303.yaml"

    assert module.validate_fixture(path) == []


def test_validator_rejects_concatenated_urls(tmp_path: Path) -> None:
    module = _load_validator()
    path = tmp_path / "urls.yaml"
    path.write_text(
        "expected_url_count: 1\nurls:\n  - https://one.example/ - https://two.example/\n",
        encoding="utf-8",
    )

    assert any("concatenated" in error for error in module.validate_fixture(path))
