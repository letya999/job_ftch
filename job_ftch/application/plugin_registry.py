from threading import Lock

from .plugin import (
    DuplicatePlugin,
    PluginDescriptor,
    PluginEntry,
    PluginKind,
    PluginNotFound,
    PluginState,
)


class PluginRegistry:
    def __init__(self) -> None:
        self._entries: dict[tuple[PluginKind, str], PluginEntry] = {}
        self._lock = Lock()

    def register(self, descriptor: PluginDescriptor, *, overwrite: bool = False) -> None:
        key = (descriptor.kind, descriptor.name)
        with self._lock:
            if key in self._entries and not overwrite:
                raise DuplicatePlugin(f"{descriptor.kind}:{descriptor.name}")
            self._entries[key] = PluginEntry(descriptor=descriptor)

    def resolve(self, kind: PluginKind, name: str) -> PluginDescriptor:
        entry = self._entries.get((kind, name))
        if entry is None:
            raise PluginNotFound(f"{kind}:{name}")
        return entry.descriptor

    def list_by_kind(self, kind: PluginKind) -> list[PluginEntry]:
        return [e for (k, _), e in self._entries.items() if k == kind]

    def all_entries(self) -> list[PluginEntry]:
        return list(self._entries.values())

    def mark_state(
        self, kind: PluginKind, name: str, state: PluginState, error: Exception | None = None
    ) -> None:
        entry = self._entries.get((kind, name))
        if entry is not None:
            entry.state = state
            entry.error = error

    def reset(self) -> None:
        """Test helper — clears all registrations."""
        with self._lock:
            self._entries.clear()


_default_registry = PluginRegistry()
