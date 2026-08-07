from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import cast

import pytest

from job_ftch import PipelineBuilder
from job_ftch.adapters.dagster.adapter import create_definitions
from job_ftch.adapters.fastapi.adapter import create_app
from job_ftch.adapters.faststream.adapter import register_faststream_handlers
from job_ftch.adapters.mcp.adapter import create_mcp_server
from job_ftch.domain.source_spec import LocalFixtureSpec
from job_ftch.nodes import SanitizeNode
from job_ftch.nodes.triage import HeuristicTriageNode


class _DummyStore:
    async def has_processed(self, item):  # type: ignore[no-untyped-def]
        del item
        return False

    async def mark_processed(self, item):  # type: ignore[no-untyped-def]
        del item

    async def has_dedup_key(self, kind, key):  # type: ignore[no-untyped-def]
        del kind, key
        return False

    async def remember_dedup_key(self, record):  # type: ignore[no-untyped-def]
        del record

    async def list_dedup_keys(self):  # type: ignore[no-untyped-def]
        return []

    async def record_duplicate(self, record):  # type: ignore[no-untyped-def]
        del record

    async def list_duplicate_records(self):  # type: ignore[no-untyped-def]
        return []

    async def get_run_state(self, key):  # type: ignore[no-untyped-def]
        del key
        return None

    async def set_run_state(self, key, value):  # type: ignore[no-untyped-def]
        del key, value


class _DummySink:
    async def emit(self, item):  # type: ignore[no-untyped-def]
        del item


class _DummyBuilder:
    def __init__(self) -> None:
        self.specs: list[LocalFixtureSpec] = []

    def clone(self) -> _DummyBuilder:
        return _DummyBuilder()

    def sources(self, specs):  # type: ignore[no-untyped-def]
        self.specs = list(specs)
        return self

    async def run_async(self):  # type: ignore[no-untyped-def]
        class _Summary:
            def as_dict(self) -> dict[str, object]:
                return {"emitted": 1}

        return _Summary()

    def get_store(self):  # type: ignore[no-untyped-def]
        class _Store:
            async def get_run_state(self, key):  # type: ignore[no-untyped-def]
                return f"value:{key}"

        return _Store()


def test_pipeline_builder_requires_sanitize_first() -> None:
    builder = PipelineBuilder()
    builder.source(LocalFixtureSpec(path="fixtures/debug/raw_items.json"))
    builder.store(_DummyStore())
    builder.stage(HeuristicTriageNode())
    builder.sink(_DummySink())

    with pytest.raises(ValueError, match=r"first pipeline stage must be a SanitizeNode"):
        builder.build()


def test_pipeline_builder_builds_with_sanitize_first() -> None:
    builder = PipelineBuilder()
    builder.source(LocalFixtureSpec(path="fixtures/debug/raw_items.json"))
    builder.store(_DummyStore())
    builder.stage(SanitizeNode())
    builder.stage(HeuristicTriageNode())
    builder.sink(_DummySink())

    pipeline = builder.build()

    assert pipeline is not None


def test_fastapi_adapter_registers_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    routes: list[tuple[str, str]] = []

    class FakeFastAPI:
        def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
            self.kwargs = kwargs

        def post(self, path):  # type: ignore[no-untyped-def]
            def decorator(func):  # type: ignore[no-untyped-def]
                routes.append(("POST", path))
                return func

            return decorator

        def get(self, path):  # type: ignore[no-untyped-def]
            def decorator(func):  # type: ignore[no-untyped-def]
                routes.append(("GET", path))
                return func

            return decorator

    monkeypatch.setitem(
        sys.modules,
        "fastapi",
        SimpleNamespace(FastAPI=FakeFastAPI, HTTPException=RuntimeError),
    )

    app = create_app(_DummyBuilder())

    assert app.kwargs["title"] == "job_ftch"
    assert ("POST", "/pipeline/run") in routes
    assert ("GET", "/pipeline/status") in routes
    assert ("GET", "/pipeline/sources") in routes
    assert ("GET", "/jobs/search") in routes


def test_mcp_adapter_registers_tool_and_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    # create_mcp_server is a deprecated shim over TenantMCPServer.
    # TenantMCPServer registers 18 tools and 3 resources.
    registered: dict[str, object] = {"tools": 0, "resources": 0, "uris": []}

    class FakeMCP:
        def __init__(self, name, **kwargs):  # type: ignore[no-untyped-def]
            self.name = name

        def tool(self, func=None, **kwargs):  # type: ignore[no-untyped-def]
            def decorator(fn):  # type: ignore[no-untyped-def]
                registered["tools"] = int(registered["tools"]) + 1  # type: ignore[arg-type]
                return fn

            if func is not None and callable(func):
                return decorator(func)
            return decorator

        def resource(self, uri):  # type: ignore[no-untyped-def]
            def decorator(func):  # type: ignore[no-untyped-def]
                registered["resources"] = int(registered["resources"]) + 1  # type: ignore[arg-type]
                cast("list[str]", registered["uris"]).append(uri)
                return func

            return decorator

    monkeypatch.setitem(sys.modules, "fastmcp", SimpleNamespace(FastMCP=FakeMCP))

    import warnings

    from job_ftch.config import Settings

    safe_settings = Settings(llm_backend="heuristic", store_backend="memory")
    monkeypatch.setattr(
        "job_ftch.adapters.mcp.server.get_settings",
        lambda: safe_settings,
    )

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        server = create_mcp_server(_DummyBuilder())
        assert any(issubclass(warning.category, DeprecationWarning) for warning in w)

    assert server.name == "job_ftch"
    # TenantMCPServer surface: 18 tools, 3 resources
    assert registered["tools"] == 18
    assert registered["resources"] == 3
    uris = cast("list[str]", registered["uris"])
    assert any("jobs://" in uri for uri in uris)


def test_faststream_adapter_registers_handler() -> None:
    handlers: list[tuple[str, str]] = []

    class FakeBroker:
        def subscriber(self, subject):  # type: ignore[no-untyped-def]
            def decorator(func):  # type: ignore[no-untyped-def]
                handlers.append(("sub", subject))
                return func

            return decorator

        def publisher(self, subject):  # type: ignore[no-untyped-def]
            def decorator(func):  # type: ignore[no-untyped-def]
                handlers.append(("pub", subject))
                return func

            return decorator

    handler = register_faststream_handlers(
        FakeBroker(),
        subject="jobs.trigger",
        publish_subject="jobs.results",
        builder=_DummyBuilder(),
    )

    assert handler is not None
    assert ("sub", "jobs.trigger") in handlers
    assert ("pub", "jobs.results") in handlers


def test_dagster_adapter_creates_definitions(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDefinitions:
        def __init__(self, *, assets):  # type: ignore[no-untyped-def]
            self.assets = assets

    class FakeMaterializeResult:
        def __init__(self, *, metadata):  # type: ignore[no-untyped-def]
            self.metadata = metadata

    def fake_asset(*, name):  # type: ignore[no-untyped-def]
        def decorator(func):  # type: ignore[no-untyped-def]
            func.asset_name = name
            return func

        return decorator

    monkeypatch.setitem(
        sys.modules,
        "dagster",
        SimpleNamespace(
            Definitions=FakeDefinitions,
            MaterializeResult=FakeMaterializeResult,
            asset=fake_asset,
        ),
    )

    defs = create_definitions(
        _DummyBuilder(), [LocalFixtureSpec(path="fixtures/debug/raw_items.json")]
    )

    assert len(defs.assets) == 1
    assert defs.assets[0].asset_name == "local_fixture_0"
