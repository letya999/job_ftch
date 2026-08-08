"""Unit-level MCP adapter tests (no full pipeline run)."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from job_ftch.adapters.mcp.server import (
    _json_default,
    _tool_annotations,
    create_server,
    probe_llm_backend,
)
from job_ftch.config import Settings


def _fake_mcp_module() -> type:
    class FakeMCP:
        def __init__(self, name: str, **kwargs: Any) -> None:
            self.name = name
            self.kwargs = kwargs
            self.tools: dict[str, object] = {}
            self.resources: dict[str, object] = {}
            self.run_kwargs: dict[str, object] | None = None

        def tool(self, func=None, **kwargs):  # type: ignore[no-untyped-def]
            def decorator(fn):  # type: ignore[no-untyped-def]
                self.tools[fn.__name__] = fn
                return fn

            if func is not None and callable(func):
                return decorator(func)
            return decorator

        def resource(self, uri: str):  # type: ignore[no-untyped-def]
            def decorator(func):  # type: ignore[no-untyped-def]
                self.resources[uri] = func
                return func

            return decorator

        def run(self, **kwargs: Any) -> None:
            self.run_kwargs = kwargs

    return FakeMCP


def _minimal_settings(**overrides: Any) -> Settings:
    base = {
        "llm_backend": "heuristic",
        "store_backend": "memory",
        "job_backend": "sqlite",
        "search_backend": "sqlite",
        "job_group_store_backend": "sqlite",
    }
    base.update(overrides)
    return Settings(**base)


def _tenant_configs(tmp_path: Path) -> Path:
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "tenant.json").write_text(
        json.dumps(
            {
                "tenant_id": "t1",
                "display_name": "T1",
                "sources": [],
                "store_backend": "sqlite",
                "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
                "job_group_store_backend": "sqlite",
                "job_backend": "sqlite",
                "search_backend": "sqlite",
                "output": {"path": str(tmp_path / "out" / "{tenant_id}.json")},
            }
        ),
        encoding="utf-8",
    )
    return configs


def test_json_default_handles_common_types() -> None:
    class Model:
        def model_dump(self, mode: str = "python") -> dict[str, str]:
            return {"x": "y"}

    assert _json_default(datetime(2026, 1, 2, tzinfo=UTC)).startswith("2026-01-02")
    assert _json_default(Model()) == {"x": "y"}
    assert _json_default(Path("a/b")) == "a/b" or "a" in str(_json_default(Path("a/b")))


def test_tool_annotations_returns_object_or_none() -> None:
    ann = _tool_annotations(read_only=True, idempotent=True)
    # With mcp installed in test env this is ToolAnnotations; without, None.
    if ann is not None:
        assert ann.readOnlyHint is True
        assert ann.idempotentHint is True


def test_create_server_requires_fastmcp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import builtins

    real_import = builtins.__import__

    def blocked(name: str, *args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        if name == "fastmcp" or name.startswith("fastmcp."):
            raise ImportError("no fastmcp")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    # Ensure cached module is gone
    monkeypatch.delitem(sys.modules, "fastmcp", raising=False)
    with pytest.raises(ImportError, match="mcp"):
        create_server(configs_dir=tmp_path, base_settings=_minimal_settings())


def test_require_runner_before_startup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setitem(sys.modules, "fastmcp", SimpleNamespace(FastMCP=_fake_mcp_module()))
    server = create_server(configs_dir=tmp_path, base_settings=_minimal_settings())
    with pytest.raises(RuntimeError, match="startup"):
        server._require_runner()


@pytest.mark.asyncio
async def test_startup_shutdown_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setitem(sys.modules, "fastmcp", SimpleNamespace(FastMCP=_fake_mcp_module()))
    settings = _minimal_settings()
    for target in (
        "job_ftch.config.get_settings",
        "job_ftch.application.builder.get_settings",
        "job_ftch.application.pipeline.get_settings",
    ):
        monkeypatch.setattr(target, lambda s=settings: s)

    configs = _tenant_configs(tmp_path)
    server = create_server(configs_dir=configs, base_settings=settings)
    await server.startup()
    first = server.runner
    await server.startup()
    assert server.runner is first
    await server.shutdown()
    assert server.runner is None
    await server.shutdown()
    assert server.runner is None


def test_run_delegates_to_fastmcp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setitem(sys.modules, "fastmcp", SimpleNamespace(FastMCP=_fake_mcp_module()))
    server = create_server(configs_dir=tmp_path, base_settings=_minimal_settings())
    server.run(transport="streamable-http", host="0.0.0.0", port=9001)
    assert server.app.run_kwargs == {
        "transport": "streamable-http",
        "host": "0.0.0.0",
        "port": 9001,
    }
    server.run(transport="stdio")
    assert server.app.run_kwargs == {"transport": "stdio"}


@pytest.mark.asyncio
async def test_probe_llm_backend_non_openai() -> None:
    result = await probe_llm_backend(_minimal_settings(llm_backend="heuristic"))
    assert result["ok"] is True
    assert "does not use OpenAI" in (result["error"] or "")


@pytest.mark.asyncio
async def test_probe_llm_backend_missing_base_url() -> None:
    settings = _minimal_settings(
        llm_backend="openai",
        openai_api_key="k",  # type: ignore[arg-type]
        openai_base_url=None,
        openai_model="m",
    )
    result = await probe_llm_backend(settings)
    assert result["ok"] is False
    assert "empty" in (result["error"] or "")


@pytest.mark.asyncio
async def test_probe_llm_backend_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        status_code = 503

        def json(self) -> dict[str, object]:
            return {}

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str] | None = None) -> _Resp:
            return _Resp()

    monkeypatch.setattr("httpx.AsyncClient", _Client)
    settings = _minimal_settings(
        llm_backend="openai",
        openai_api_key="k",  # type: ignore[arg-type]
        openai_base_url="http://127.0.0.1:8317/v1",
        openai_model="gpt",
    )
    result = await probe_llm_backend(settings)
    assert result["ok"] is False
    assert "503" in (result["error"] or "")


@pytest.mark.asyncio
async def test_probe_llm_backend_model_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {"data": [{"id": "other-model"}, "skip-me", {"id": 1}]}

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str] | None = None) -> _Resp:
            return _Resp()

    monkeypatch.setattr("httpx.AsyncClient", _Client)
    settings = _minimal_settings(
        llm_backend="openai",
        openai_api_key="k",  # type: ignore[arg-type]
        openai_base_url="http://127.0.0.1:8317/v1",
        openai_model="missing-model",
    )
    result = await probe_llm_backend(settings)
    assert result["ok"] is True
    assert result["reachable"] is True
    assert "missing-model" in (result["error"] or "")


@pytest.mark.asyncio
async def test_probe_llm_backend_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str] | None = None) -> object:
            raise ConnectionError("refused")

    monkeypatch.setattr("httpx.AsyncClient", _Client)
    settings = _minimal_settings(
        llm_backend="openai",
        openai_api_key="k",  # type: ignore[arg-type]
        openai_base_url="http://127.0.0.1:8317/v1",
        openai_model="m",
    )
    result = await probe_llm_backend(settings)
    assert result["ok"] is False
    assert "ConnectionError" in (result["error"] or "")


@pytest.mark.asyncio
async def test_deprecated_adapter_shim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setitem(sys.modules, "fastmcp", SimpleNamespace(FastMCP=_fake_mcp_module()))
    settings = _minimal_settings()
    monkeypatch.setattr(
        "job_ftch.adapters.mcp.server.get_settings",
        lambda: settings,
    )
    from job_ftch.adapters.mcp.adapter import create_mcp_server

    with pytest.warns(DeprecationWarning, match="create_mcp_server"):
        server = create_mcp_server(SimpleNamespace())
    assert server.name == "job_ftch"


@pytest.mark.asyncio
async def test_lifespan_starts_and_stops_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setitem(sys.modules, "fastmcp", SimpleNamespace(FastMCP=_fake_mcp_module()))
    settings = _minimal_settings()
    for target in (
        "job_ftch.config.get_settings",
        "job_ftch.application.builder.get_settings",
    ):
        monkeypatch.setattr(target, lambda s=settings: s)

    configs = _tenant_configs(tmp_path)
    server = create_server(configs_dir=configs, base_settings=settings)
    lifespan = server.app.kwargs.get("lifespan")
    assert lifespan is not None
    async with lifespan(server.app):
        assert server.runner is not None
    assert server.runner is None
