"""Tests for create_store_with_fallback registry behaviour (ADR-020)."""

from __future__ import annotations

import pytest

from job_ftch.application.registry import (
    _FALLBACK_STORE_BACKEND,
    StorageError,
    _store_factories,
    create_store_with_fallback,
)
from job_ftch.config import Settings


@pytest.mark.asyncio
async def test_fallback_store_backend_is_registered() -> None:
    """memory must always be registered after load_extensions."""
    from job_ftch.application.registry import load_extensions

    load_extensions()
    assert _FALLBACK_STORE_BACKEND in _store_factories, (
        f"'{_FALLBACK_STORE_BACKEND}' must be registered; boundary fix depends on it"
    )


@pytest.mark.asyncio
async def test_healthy_primary_store_is_returned() -> None:
    """When primary store passes ping, no fallback is used."""
    settings = Settings(store_backend="memory", store_fallback_on_error=True)
    store = await create_store_with_fallback(settings)
    # InMemoryStore is returned directly because ping() succeeds (returns True)
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    assert isinstance(store, InMemoryStore)


@pytest.mark.asyncio
async def test_fallback_on_creation_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """If primary store creation raises, fallback to memory when flag is set."""
    from job_ftch.application.registry import load_extensions

    load_extensions()

    def _broken_factory(s: Settings) -> object:
        raise RuntimeError("intentional failure")

    # Register a fake backend that always fails
    _store_factories["__test_broken__"] = _broken_factory
    try:
        settings = Settings(store_backend="__test_broken__", store_fallback_on_error=True)
        store = await create_store_with_fallback(settings)
        from job_ftch.infrastructure.stores.in_memory import InMemoryStore

        assert isinstance(store, InMemoryStore)
    finally:
        _store_factories.pop("__test_broken__", None)


@pytest.mark.asyncio
async def test_no_fallback_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """If store_fallback_on_error is False, the original exception propagates."""
    from job_ftch.application.registry import load_extensions

    load_extensions()

    def _broken_factory(s: Settings) -> object:
        raise RuntimeError("intentional failure")

    _store_factories["__test_broken2__"] = _broken_factory
    try:
        settings = Settings(store_backend="__test_broken2__", store_fallback_on_error=False)
        with pytest.raises(RuntimeError, match="intentional failure"):
            await create_store_with_fallback(settings)
    finally:
        _store_factories.pop("__test_broken2__", None)


@pytest.mark.asyncio
async def test_auto_backend_resolving_to_postgres_does_not_silently_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BrokenPostgresStore:
        async def ping(self) -> bool:
            return False

        async def get(self, key: str) -> str | None:
            del key
            return None

        async def set(self, key: str, value: str) -> None:
            del key, value

        async def delete(self, key: str) -> None:
            del key

        async def set_add(self, key: str, member: str) -> None:
            del key, member

        async def set_contains(self, key: str, member: str) -> bool:
            del key, member
            return False

        async def clear_set(self, key: str) -> None:
            del key

        async def set_members(self, key: str) -> tuple[str, ...]:
            del key
            return ()

    monkeypatch.setattr(
        "job_ftch.application.registry.create_store",
        lambda settings: _BrokenPostgresStore(),
    )
    settings = Settings(
        store_backend="auto",
        store_dsn="postgresql://user:pass@localhost/db",
        store_fallback_on_error=True,
        store_allow_fallback=False,
    )

    with pytest.raises(StorageError, match="Postgres configured but unreachable"):
        await create_store_with_fallback(settings)


@pytest.mark.asyncio
async def test_postgres_auto_fallback_can_be_explicitly_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BrokenPostgresStore:
        async def ping(self) -> bool:
            return False

        async def get(self, key: str) -> str | None:
            del key
            return None

        async def set(self, key: str, value: str) -> None:
            del key, value

        async def delete(self, key: str) -> None:
            del key

        async def set_add(self, key: str, member: str) -> None:
            del key, member

        async def set_contains(self, key: str, member: str) -> bool:
            del key, member
            return False

        async def clear_set(self, key: str) -> None:
            del key

        async def set_members(self, key: str) -> tuple[str, ...]:
            del key
            return ()

    monkeypatch.setattr(
        "job_ftch.application.registry.create_store",
        lambda settings: _BrokenPostgresStore(),
    )
    settings = Settings(
        store_backend="auto",
        store_dsn="postgresql://user:pass@localhost/db",
        store_fallback_on_error=True,
        store_allow_fallback=True,
    )

    store = await create_store_with_fallback(settings)
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    assert isinstance(store, InMemoryStore)


@pytest.mark.asyncio
async def test_fallback_not_an_infra_import() -> None:
    """application/registry.py must not import from job_ftch.infrastructure at module level."""
    import importlib
    import inspect

    registry_module = importlib.import_module("job_ftch.application.registry")
    source = inspect.getsource(registry_module)

    # Top-level (non-TYPE_CHECKING) infrastructure imports must not exist
    lines = source.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if "from job_ftch.infrastructure" in stripped and "TYPE_CHECKING" not in stripped:
            # Allow inside function bodies only if it's the known pre-existing one
            # (there should be none after ADR-020)
            pytest.fail(
                f"Forbidden infrastructure import at line {i + 1}: {line!r}\n"
                "application/ must not import job_ftch.infrastructure/ — see ADR-020."
            )
