from __future__ import annotations

from pathlib import Path

import yaml


def test_local_mcp_browser_probe_is_isolated_and_forces_render() -> None:
    root = Path(__file__).resolve().parents[2]
    probe_path = root / "docker/local-mcp/config/browser-probe/browser_probe.yaml"
    normal_dir = root / "docker/local-mcp/config/tenants"

    assert probe_path.parent != normal_dir
    payload = yaml.safe_load(probe_path.read_text(encoding="utf-8"))
    assert payload["tenant_id"] == "browser_probe"
    assert len(payload["sources"]) == 1
    source = payload["sources"][0]
    assert source["type"] == "career_site"
    assert source["monitor"] == "dom"
    assert source["monitor_config"]["render"] is True
