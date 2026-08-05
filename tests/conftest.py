"""Shared pytest fixtures for job_ftch test suite."""

from __future__ import annotations

import ipaddress
import socket
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from job_ftch.config import get_settings
from job_ftch.domain import (
    JobRecord,
    ProfileCatalog,
    RawItem,
    SearchProfile,
    SkillTag,
    SourceKind,
    WorkMode,
)
from job_ftch.infrastructure.stores.in_memory import InMemoryStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


class UnexpectedNetworkAccess(RuntimeError):
    """Raised when an offline test attempts an external connection."""


def _is_loopback_host(host: object) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="ignore")
    value = str(host).split("%", 1)[0].lower()
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _is_ip_literal(host: object) -> bool:
    if not isinstance(host, (str, bytes)):
        return False
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="ignore")
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# Shared test helpers (not fixtures — import directly when needed)
# ---------------------------------------------------------------------------


class StubSource:
    """Universal stub source for tests."""

    def __init__(self, items: list[RawItem]) -> None:
        self._items = items

    def fetch(self) -> AsyncIterator[RawItem]:
        async def _gen() -> AsyncIterator[RawItem]:
            for item in self._items:
                yield item

        return _gen()


class CollectSink:
    """Sink that collects all emitted items for assertions."""

    def __init__(self) -> None:
        self.items: list[RawItem] = []

    async def emit(self, item: RawItem) -> None:
        self.items.append(item)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-network",
        action="store_true",
        default=False,
        help="run tests requiring real network access",
    )
    parser.addoption(
        "--run-telegram",
        action="store_true",
        default=False,
        help="run tests requiring Telegram credentials",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    run_network = config.getoption("--run-network")
    run_telegram = config.getoption("--run-telegram")
    e2e_root = Path(__file__).parent / "e2e"
    skip_network = pytest.mark.skip(reason="need --run-network option to run")
    skip_telegram = pytest.mark.skip(reason="need --run-telegram option to run")

    for item in items:
        if item.path.is_relative_to(e2e_root):
            item.add_marker(pytest.mark.e2e)
        if "network" in item.keywords and not run_network:
            item.add_marker(skip_network)
        if "telegram" in item.keywords and not run_telegram:
            item.add_marker(skip_telegram)


@pytest.fixture
def make_raw_item() -> Any:
    """Fixture for make_raw_item helper."""

    def _make(
        *,
        external_id: str = "1",
        text: str = "Senior ML Engineer\nRemote\nBuild AI systems",
        source_kind: SourceKind = SourceKind.DEBUG,
        source_name: str = "debug",
        **kwargs: object,
    ) -> RawItem:
        """Factory for RawItem with sensible defaults."""
        return RawItem(
            source_kind=source_kind,
            source_name=source_name,
            external_id=external_id,
            text=text,
            **kwargs,  # type: ignore[arg-type]
        )

    return _make


@pytest.fixture
def make_job_record() -> Any:
    """Fixture for make_job_record helper."""

    def _make(**kwargs: object) -> JobRecord:
        """Factory with sensible defaults for JobRecord."""
        defaults: dict[str, object] = dict(
            raw_item_id="r1",
            source_kind=SourceKind.DEBUG,
            source_name="debug",
            title="Senior ML Engineer",
            company="OpenAI",
            description="Build large-scale ML systems for production use.",
            work_mode=WorkMode.REMOTE,
            relevance_score=0.7,
            quality_score=0.8,
        )
        defaults.update(kwargs)
        return JobRecord(**defaults)  # type: ignore[arg-type]

    return _make


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path to repo root — use instead of relative Path(...)."""
    return Path(__file__).resolve().parent.parent


@pytest.fixture
def stub_source_factory() -> type[StubSource]:
    """Factory fixture: stub_source_factory([item1, item2])."""
    return StubSource


@pytest.fixture
def collect_sink() -> CollectSink:
    return CollectSink()


@pytest.fixture
def memory_store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def minimal_profile() -> SearchProfile:
    return SearchProfile(
        profile_id="test_ml",
        name="ML Engineer",
        target_roles=("ML Engineer", "Data Scientist"),
        required_skills=(SkillTag(canonical_name="python"),),
        preferred_skills=(SkillTag(canonical_name="pytorch"),),
        relevance_threshold=0.45,
    )


@pytest.fixture
def minimal_catalog(minimal_profile: SearchProfile) -> ProfileCatalog:
    return ProfileCatalog(profiles=[minimal_profile])


@pytest.fixture(autouse=True)
def default_test_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep unit tests independent from the developer's live services.

    The product default is OpenAI. Tests keep that default too, but use a
    synthetic JOB_FTCH_OPENAI_API_KEY so settings construction does not depend
    on a developer's private .env. Offline network guards still prevent any
    unmarked test from calling the real OpenAI API.
    """
    monkeypatch.setenv("JOB_FTCH_STORE_BACKEND", "memory")
    monkeypatch.setenv("JOB_FTCH_JOB_BACKEND", "sqlite")
    monkeypatch.setenv("JOB_FTCH_RELEVANCE_SHOT_BACKEND", "memory")
    monkeypatch.setenv("JOB_FTCH_OPENAI_API_KEY", "sk-test-offline-pytest-openai-key")
    monkeypatch.delenv("JOB_FTCH_LLM_BACKEND", raising=False)
    monkeypatch.delenv("JOB_FTCH_VECTOR_BACKEND", raising=False)
    monkeypatch.delenv("JOB_FTCH_QDRANT_URL", raising=False)
    monkeypatch.delenv("JOB_FTCH_QDRANT_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def block_unmarked_external_network(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail offline tests at the socket boundary while allowing loopback I/O."""
    network_enabled = request.node.get_closest_marker("network") and request.config.getoption(
        "--run-network"
    )
    telegram_enabled = request.node.get_closest_marker("telegram") and request.config.getoption(
        "--run-telegram"
    )
    if network_enabled or telegram_enabled:
        return

    original_getaddrinfo = socket.getaddrinfo
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def guarded_getaddrinfo(host: object, *args: Any, **kwargs: Any) -> Any:
        if _is_loopback_host(host) or _is_ip_literal(host):
            return original_getaddrinfo(host, *args, **kwargs)
        raise UnexpectedNetworkAccess(f"external DNS lookup blocked in offline test: {host!r}")

    def guarded_connect(sock: socket.socket, address: Any) -> None:
        if not isinstance(address, tuple) or _is_loopback_host(address[0]):
            return original_connect(sock, address)
        raise UnexpectedNetworkAccess(
            f"external socket connection blocked in offline test: {address!r}"
        )

    def guarded_connect_ex(sock: socket.socket, address: Any) -> int:
        if not isinstance(address, tuple) or _is_loopback_host(address[0]):
            return original_connect_ex(sock, address)
        raise UnexpectedNetworkAccess(
            f"external socket connection blocked in offline test: {address!r}"
        )

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
