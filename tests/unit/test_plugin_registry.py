import pytest
from job_ftch.application.plugin import (
    PluginKind,
    PluginDescriptor,
    PluginState,
    PluginNotFound,
    DuplicatePlugin,
)
from job_ftch.application.plugin_registry import PluginRegistry

def test_register_and_resolve_happy_path() -> None:
    registry = PluginRegistry()
    def my_factory() -> str: return "hello"
    
    descriptor = PluginDescriptor(
        name="test",
        kind=PluginKind.SOURCE,
        factory=my_factory
    )
    
    registry.register(descriptor)
    resolved = registry.resolve(PluginKind.SOURCE, "test")
    
    assert resolved == descriptor
    assert resolved.factory() == "hello"

def test_duplicate_raises_duplicate_plugin() -> None:
    registry = PluginRegistry()
    descriptor = PluginDescriptor(
        name="test",
        kind=PluginKind.SOURCE,
        factory=lambda: None
    )
    
    registry.register(descriptor)
    with pytest.raises(DuplicatePlugin):
        registry.register(descriptor)

def test_overwrite_allowed_when_flag_set() -> None:
    registry = PluginRegistry()
    d1 = PluginDescriptor(name="test", kind=PluginKind.SOURCE, factory=lambda: 1)
    d2 = PluginDescriptor(name="test", kind=PluginKind.SOURCE, factory=lambda: 2)
    
    registry.register(d1)
    registry.register(d2, overwrite=True)
    
    assert registry.resolve(PluginKind.SOURCE, "test").factory() == 2

def test_not_found_raises_plugin_not_found() -> None:
    registry = PluginRegistry()
    with pytest.raises(PluginNotFound):
        registry.resolve(PluginKind.SOURCE, "missing")

def test_mark_state_transitions() -> None:
    registry = PluginRegistry()
    descriptor = PluginDescriptor(name="test", kind=PluginKind.SOURCE, factory=lambda: None)
    registry.register(descriptor)
    
    entries = registry.all_entries()
    assert entries[0].state == PluginState.PENDING
    
    registry.mark_state(PluginKind.SOURCE, "test", PluginState.ACTIVE)
    assert entries[0].state == PluginState.ACTIVE
    
    err = ValueError("fail")
    registry.mark_state(PluginKind.SOURCE, "test", PluginState.ERROR, error=err)
    assert entries[0].state == PluginState.ERROR
    assert entries[0].error == err

def test_list_by_kind_filters_correctly() -> None:
    registry = PluginRegistry()
    registry.register(PluginDescriptor(name="src", kind=PluginKind.SOURCE, factory=lambda: None))
    registry.register(PluginDescriptor(name="snk", kind=PluginKind.SINK, factory=lambda: None))
    
    sources = registry.list_by_kind(PluginKind.SOURCE)
    assert len(sources) == 1
    assert sources[0].descriptor.name == "src"
    
    sinks = registry.list_by_kind(PluginKind.SINK)
    assert len(sinks) == 1
    assert sinks[0].descriptor.name == "snk"

def test_all_entries_returns_full_snapshot() -> None:
    registry = PluginRegistry()
    registry.register(PluginDescriptor(name="p1", kind=PluginKind.SOURCE, factory=lambda: None))
    registry.register(PluginDescriptor(name="p2", kind=PluginKind.SINK, factory=lambda: None))
    registry.register(PluginDescriptor(name="p3", kind=PluginKind.LLM, factory=lambda: None))
    
    entries = registry.all_entries()
    assert len(entries) == 3

def test_reset_clears_all() -> None:
    registry = PluginRegistry()
    registry.register(PluginDescriptor(name="test", kind=PluginKind.SOURCE, factory=lambda: None))
    
    registry.reset()
    assert len(registry.all_entries()) == 0
    with pytest.raises(PluginNotFound):
        registry.resolve(PluginKind.SOURCE, "test")
