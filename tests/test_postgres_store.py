import os

import pytest

from job_ftch.application.contracts import StoreConnector
from job_ftch.infrastructure.stores.postgres import PostgreSQLStore

pytestmark = pytest.mark.network


@pytest.mark.asyncio
async def test_postgres_store_connectivity():
    dsn = os.getenv("JOB_FTCH_STORE_DSN")
    if not dsn:
        pytest.skip("JOB_FTCH_STORE_DSN not set")

    store = PostgreSQLStore(dsn)
    try:
        assert await store.ping() is True
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_postgres_store_interface():
    dsn = os.getenv("JOB_FTCH_STORE_DSN")
    if not dsn:
        pytest.skip("JOB_FTCH_STORE_DSN not set")

    store = PostgreSQLStore(dsn)
    try:
        assert isinstance(store, StoreConnector)
        # Test basic KV
        await store.set("test_key", "test_value")
        assert await store.get("test_key") == "test_value"
        await store.delete("test_key")
        assert await store.get("test_key") is None

        # Test Set
        await store.set_add("test_set", "member1")
        assert await store.set_contains("test_set", "member1") is True
        assert "member1" in await store.set_members("test_set")
    finally:
        await store.close()
