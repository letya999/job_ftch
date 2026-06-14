"""Shared pytest fixtures for job_ftch test suite."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

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
    from collections.abc import AsyncIterator


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
def default_test_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure tests use memory store by default to avoid Postgres DSN requirement."""
    monkeypatch.setenv("JOB_FTCH_STORE_BACKEND", "memory")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
