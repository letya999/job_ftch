"""Smoke tests — verify project structure and imports work."""

import importlib


def test_domain_importable() -> None:
    """domain package must be importable with zero side effects."""
    mod = importlib.import_module("domain")
    assert mod is not None


def test_application_importable() -> None:
    mod = importlib.import_module("application")
    assert mod is not None


def test_infrastructure_importable() -> None:
    mod = importlib.import_module("infrastructure")
    assert mod is not None


def test_nodes_importable() -> None:
    mod = importlib.import_module("nodes")
    assert mod is not None


def test_sinks_importable() -> None:
    mod = importlib.import_module("sinks")
    assert mod is not None


def test_config_loads() -> None:
    """Settings must load with defaults when no .env present."""
    from config import Settings

    s = Settings()
    assert s.store_backend == "memory"
    assert s.log_level == "DEBUG"
