"""Tests for IncrementalCursor watermark isolation."""

import pytest

from job_ftch.application.watermark import IncrementalCursor


class FakeStoreConnector:
    def __init__(self):
        self.data = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str) -> None:
        self.data[key] = value

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


class TestIncrementalCursorIsolation:
    """Verifies that cursors with different namespaces don't collide."""

    def test_key_format_includes_namespace_and_source(self):
        connector = FakeStoreConnector()
        cursor = IncrementalCursor(connector, namespace="tenant-a")
        # _key is internal but we need to verify it for isolation
        key = cursor._key("hh_ru")
        assert "tenant-a" in key
        assert "hh_ru" in key
        assert key.endswith(":cursor")

    def test_different_namespaces_produce_different_keys(self):
        connector = FakeStoreConnector()
        a = IncrementalCursor(connector, namespace="tenant-a")
        b = IncrementalCursor(connector, namespace="tenant-b")
        assert a._key("hh_ru") != b._key("hh_ru")

    def test_same_namespace_different_sources_produce_different_keys(self):
        connector = FakeStoreConnector()
        a = IncrementalCursor(connector, namespace="tenant-a")
        assert a._key("hh_ru") != a._key("hh_kz")

    def test_empty_namespace_differs_from_named(self):
        connector = FakeStoreConnector()
        a = IncrementalCursor(connector, namespace="")
        b = IncrementalCursor(connector, namespace="tenant-a")
        assert a._key("hh_ru") != b._key("hh_ru")

    @pytest.mark.asyncio
    async def test_set_on_one_namespace_invisible_to_other(self):
        """Core isolation invariant: tenant-A's cursor must not affect tenant-B."""
        connector = FakeStoreConnector()
        cursor_a = IncrementalCursor(connector, namespace="tenant-a")
        cursor_b = IncrementalCursor(connector, namespace="tenant-b")

        await cursor_a.set("src1", "2026-01-01T00:00:00")

        assert await cursor_a.get("src1") == "2026-01-01T00:00:00"
        assert await cursor_b.get("src1") is None

    @pytest.mark.asyncio
    async def test_cursor_reset_clears_only_own_namespace(self):
        """Resetting one cursor must not affect cursors with different namespace."""
        connector = FakeStoreConnector()
        cursor_a = IncrementalCursor(connector, namespace="tenant-a")
        cursor_b = IncrementalCursor(connector, namespace="tenant-b")

        await cursor_a.set("src1", "val-a")
        await cursor_b.set("src1", "val-b")

        await cursor_a.reset("src1")

        assert await cursor_a.get("src1") is None
        assert await cursor_b.get("src1") == "val-b"
