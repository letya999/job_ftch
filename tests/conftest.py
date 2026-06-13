"""Shared pytest fixtures for job_ftch test suite."""

import pytest


@pytest.fixture(autouse=True)
def default_test_settings(monkeypatch):
    """Ensure tests use memory store by default to avoid Postgres DSN requirement."""
    monkeypatch.setenv("JOB_FTCH_STORE_BACKEND", "memory")


@pytest.fixture
def anyio_backend():
    return "asyncio"
