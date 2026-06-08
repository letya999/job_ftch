"""Smoke tests — verify project structure and imports work."""

import importlib
import os

from pytest import MonkeyPatch


def test_domain_importable() -> None:
    """job_ftch.domain package must be importable with zero side effects."""
    mod = importlib.import_module("job_ftch.domain")
    assert mod is not None


def test_application_importable() -> None:
    mod = importlib.import_module("job_ftch.application")
    assert mod is not None


def test_infrastructure_importable() -> None:
    mod = importlib.import_module("job_ftch.infrastructure")
    assert mod is not None


def test_nodes_importable() -> None:
    mod = importlib.import_module("job_ftch.nodes")
    assert mod is not None


def test_sinks_importable() -> None:
    mod = importlib.import_module("job_ftch.sinks")
    assert mod is not None


def test_config_loads(monkeypatch: MonkeyPatch) -> None:
    """Settings must load with defaults when no .env present."""
    from job_ftch.config import Settings

    for key in tuple(os.environ):
        if key.startswith("JOB_FTCH_"):
            monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=None)
    assert s.store_backend == "memory"
    assert s.source_backend == "local_fixture"
    assert s.sink_backend == "json_file"
    assert s.log_level == "INFO"
