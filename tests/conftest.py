"""Shared pytest fixtures for job_ftch test suite."""

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"
