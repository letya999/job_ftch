from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize("version", [None, 8])
def test_home_proxy_preserves_credentials_and_backs_up_original(tmp_path, version):
    spec = importlib.util.spec_from_file_location(
        "configure_home", "scripts/configure_llm_home_proxy.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = {"api-keys": ["client-placeholder"], "port": 8317}
    if version:
        config["config-version"] = version
    target = tmp_path / "config.yaml"
    original = yaml.safe_dump(config)
    target.write_text(original)
    backup = module.configure(target, tmp_path / "backups")
    assert Path(backup).read_text() == original
    result = yaml.safe_load(target.read_text())
    assert result["api-keys"] == ["client-placeholder"]
    assert result["port"] == 8317
    if version:
        assert result["requests"]["proxy-url"] == "http://llm-home-egress:1055"
        assert result["routing"]["retry"]["request-retry"] == 0
    else:
        assert result["proxy-url"] == "http://llm-home-egress:1055"
        assert result["request-retry"] == 0
