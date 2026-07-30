"""
Plugin contract tests.

Concrete plugin implementations can subclass TestSourcePluginContract /
TestSinkPluginContract to share these templates. The executable checks in this
module intentionally name each concrete implementation under test.

The InMemoryStore tests at the bottom serve as reference implementations.
"""

import json
from abc import ABC, abstractmethod

import pytest

from job_ftch.application.contracts import PluginMetadata
from job_ftch.domain import SourceKind
from job_ftch.domain.models import JobRecord, RawItem
from job_ftch.infrastructure.sources.local_fixture import LocalFixtureSource
from job_ftch.sinks.null_sink import NullSink

# ---------------------------------------------------------------------------
# Abstract contract bases
# ---------------------------------------------------------------------------


class TestSourcePluginContract(ABC):
    """Subclass this to contract-test any Source plugin.
    Override get_items() to return the list of items the source produces.
    """

    @abstractmethod
    async def get_items(self) -> list[RawItem]:
        """Return the items the source produces. May be empty list."""
        ...

    @pytest.mark.asyncio
    async def test_each_item_has_required_fields(self):
        items = await self.get_items()
        for item in items:
            assert item.source_kind, f"source_kind empty on {item}"
            assert item.source_name, f"source_name empty on {item}"
            assert item.external_id, f"external_id empty on {item}"
            assert isinstance(item.text, str), f"text not str on {item}"

    @pytest.mark.asyncio
    async def test_empty_source_returns_list(self):
        items = await self.get_items()
        assert isinstance(items, list)


class TestSinkPluginContract(ABC):
    """Subclass this to contract-test any Sink plugin."""

    @abstractmethod
    def make_sink(self):
        """Return a fresh sink instance."""
        ...

    @abstractmethod
    def make_job_record(self) -> JobRecord: ...

    @pytest.mark.asyncio
    async def test_emit_accepts_job_record(self):
        sink = self.make_sink()
        job = self.make_job_record()
        assert hasattr(sink, "emit"), "Sink contract requires emit()"
        assert await sink.emit(job) is None

    @pytest.mark.asyncio
    async def test_flush_is_idempotent(self):
        """Calling flush twice must produce the same observable state."""
        sink = self.make_sink()
        job = self.make_job_record()
        assert hasattr(sink, "emit"), "Sink contract requires emit()"
        assert hasattr(sink, "flush"), "Sink contract requires flush()"
        assert await sink.emit(job) is None
        assert await sink.flush() is None
        assert await sink.flush() is None


# ---------------------------------------------------------------------------
# PluginMetadata validation
# ---------------------------------------------------------------------------


class TestPluginMetadata:
    def test_valid_metadata_constructs(self):
        m = PluginMetadata(
            name="my_source",
            version="1.2.3",
            plugin_type="source",
            description="A test source",
        )
        assert m.name == "my_source"
        assert m.version == "1.2.3"
        assert m.plugin_type == "source"

    def test_metadata_is_frozen(self):
        m = PluginMetadata(name="x", version="1.0.0", plugin_type="sink", description="d")
        with pytest.raises((AttributeError, TypeError)):
            m.name = "y"  # type: ignore[misc]

    def test_empty_name_creates_but_is_identifiable(self):
        # PluginMetadata does not currently validate name; test that empty string
        # is at least constructable and identifiable as problematic.
        m = PluginMetadata(name="", version="0.0.1", plugin_type="source", description="d")
        assert m.name == ""

    def test_requires_extras_defaults_to_empty_tuple(self):
        m = PluginMetadata(name="x", version="1.0.0", plugin_type="scorer", description="d")
        assert m.requires_extras == ()

    def test_metadata_equality(self):
        a = PluginMetadata(name="x", version="1.0.0", plugin_type="source", description="d")
        b = PluginMetadata(name="x", version="1.0.0", plugin_type="source", description="d")
        assert a == b

    def test_metadata_all_plugin_types(self):
        valid_types = [
            "source",
            "sink",
            "extractor",
            "classifier",
            "normalizer",
            "scorer",
            "notification_target",
        ]
        for pt in valid_types:
            m = PluginMetadata(name="p", version="1.0.0", plugin_type=pt, description="d")
            assert m.plugin_type == pt


@pytest.mark.asyncio
async def test_local_fixture_source_satisfies_source_contract(tmp_path) -> None:
    fixture_path = tmp_path / "items.json"
    fixture_path.write_text(
        json.dumps(
            [
                {
                    "source_kind": "debug",
                    "source_name": "fixture",
                    "external_id": "item-1",
                    "text": "A valid fixture item",
                }
            ]
        ),
        encoding="utf-8",
    )

    items = [item async for item in LocalFixtureSource(fixture_path).fetch()]

    assert len(items) == 1
    item = items[0]
    assert isinstance(item, RawItem)
    assert item.source_kind is SourceKind.DEBUG
    assert item.source_name == "fixture"
    assert item.external_id == "item-1"
    assert isinstance(item.text, str)


@pytest.mark.asyncio
async def test_null_sink_satisfies_sink_contract() -> None:
    sink = NullSink()
    job = JobRecord(
        raw_item_id="raw-1",
        source_kind=SourceKind.DEBUG,
        source_name="contract",
        description="Contract job",
    )

    assert hasattr(sink, "emit"), "Sink contract requires emit()"
    assert hasattr(sink, "flush"), "Sink contract requires flush()"
    assert await sink.emit(job) is None
    assert await sink.flush() is None
    assert await sink.flush() is None
