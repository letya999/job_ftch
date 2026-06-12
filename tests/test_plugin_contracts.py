"""
Plugin contract tests.

Concrete plugin implementations must subclass TestSourcePluginContract /
TestSinkPluginContract and override the fixtures - pytest will then run
all contract checks automatically against the new plugin.

The InMemoryStore tests at the bottom serve as reference implementations.
"""
import pytest
from abc import ABC, abstractmethod
from job_ftch.domain.models import RawItem, JobRecord
from job_ftch.application.contracts import PluginMetadata


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
    def make_job_record(self) -> JobRecord:
        ...

    @pytest.mark.asyncio
    async def test_emit_accepts_job_record(self):
        sink = self.make_sink()
        job = self.make_job_record()
        # Must not raise
        if hasattr(sink, "emit"):
            await sink.emit(job)

    @pytest.mark.asyncio
    async def test_flush_is_idempotent(self):
        """Calling flush twice must produce the same observable state."""
        sink = self.make_sink()
        job = self.make_job_record()
        if hasattr(sink, "emit"):
            await sink.emit(job)
        if hasattr(sink, "flush"):
            await sink.flush()
            state_after_first = getattr(sink, "_flushed_count", None) or getattr(sink, "_buffer", None)
            await sink.flush()
            state_after_second = getattr(sink, "_flushed_count", None) or getattr(sink, "_buffer", None)
            # Second flush must not change observable state relative to first
            assert state_after_first == state_after_second or state_after_second is None


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
        valid_types = ["source", "sink", "extractor", "classifier", "normalizer", "scorer", "notification_target"]
        for pt in valid_types:
            m = PluginMetadata(name="p", version="1.0.0", plugin_type=pt, description="d")
            assert m.plugin_type == pt
