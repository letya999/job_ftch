"""Smoke tests — verify project structure and imports work."""

import importlib
import os
import subprocess

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
    # Default llm_backend is now "openai" (ADR-029). For this smoke test
    # we explicitly use heuristic so we don't require an API key.
    s = Settings(_env_file=None, store_backend="memory", llm_backend="heuristic")  # type: ignore[call-arg]
    assert s.store_backend == "memory"
    assert s.source_backend == "local_fixture"
    assert s.sink_backend == "json_file"
    assert s.log_level == "INFO"


def test_repo_safety_layout_contract() -> None:
    subprocess.run(
        ["python", "scripts/verify_repo_safety_layout.py"],
        check=True,
    )
