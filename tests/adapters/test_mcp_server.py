from __future__ import annotations

import io
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import structlog

from job_ftch.domain import RawItem, SourceKind
from job_ftch.infrastructure.llm.heuristic import HeuristicLLMProvider

# Operator surface only (intentionally no legacy MCP aliases).
MCP_OPERATOR_TOOLS = frozenset(
    {
        "activate_profile",
        "add_source",
        "approve_search_session",
        "cancel_search_session",
        "clear_run_data",
        "create_search_session",
        "add_example",
        "clear_examples",
        "disable_source",
        "explain_search_session",
        "get_bypass_capabilities",
        "get_examples_summary",
        "get_bypass_routes",
        "get_job",
        "get_job_lineage",
        "get_llm_backend_health",
        "get_pipeline_run",
        "get_pipeline_status",
        "evaluate_prefilter",
        "get_prefilter_requirements",
        "get_prefilter_status",
        "get_search_session",
        "get_sources",
        "get_tenant_status",
        "ingest_resume",
        "list_examples",
        "list_pipeline_runs",
        "list_prefilter_artifacts",
        "list_profiles",
        "list_search_session_results",
        "list_tenants",
        "plan_search_session",
        "prepare_prefilter_dataset",
        "probe_bypass_route",
        "probe_source",
        "promote_prefilter",
        "recommend_runtime_setup",
        "remove_example",
        "reset_tenant",
        "rollback_prefilter",
        "run_browser_probe",
        "run_pipeline",
        "run_search_session",
        "run_source",
        "run_source_escalation",
        "save_profile",
        "search_jobs",
        "train_prefilter",
        "validate_prefilter_dataset",
        "validate_runtime_setup",
    }
)

MCP_LEGACY_TOOLS = frozenset(
    {
        "run_all_pipelines",
        "get_status",
        "list_source_health",
        "list_sources",
        "list_runs",
        "get_run",
        "list_browser_capabilities",
        "explain_browser_route",
        "plan_source_routes",
        "get_search_session_status",
        "list_search_results",
    }
)

_SECRET_MARKERS = (
    "api_key",
    "apikey",
    "secret",
    "password",
    "token",
    "authorization",
    "proxy_url",
    "cookie",
)


def _assert_no_secret_values(payload: object) -> None:
    """Redact-sensitive keys must not leak values in setup/prefilter payloads."""
    blob = json.dumps(payload, default=str).lower()
    # Structural field names like "missing_env" are fine; secret *values* are not.
    # Reject obvious credential-looking patterns.
    assert "sk-" not in blob
    assert "bearer " not in blob
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_l = str(key).lower()
            if any(marker in key_l for marker in _SECRET_MARKERS):
                # Allowed as empty/null or boolean readiness flags, not real secrets.
                assert value in (None, "", [], {}, False, True) or (
                    isinstance(value, list) and all(isinstance(item, str) for item in value)
                ), f"unexpected secret-bearing field {key!r}"
            _assert_no_secret_values(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_secret_values(item)


def _write_fixture(path: Path) -> None:
    item = RawItem(
        source_kind=SourceKind.DEBUG,
        source_name="fixture",
        external_id="1",
        text=(
            "Senior machine learning engineer\n"
            "Remote\n"
            "Company: OpenAI\n"
            "Python, llm, pytorch\n"
            "Salary: USD 120000 - 150000"
        ),
        metadata={"company": "OpenAI", "title": "Senior machine learning engineer"},
    )
    path.write_text(json.dumps([item.model_dump(mode="json")]), encoding="utf-8")


class _AcceptingHeuristicLLMProvider(HeuristicLLMProvider):
    async def classify(self, _prompt, _schema):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            decision="accept",
            confidence=0.95,
            reasoning="deterministic MCP fixture",
            matched_positive_aspects=(),
            mismatched_aspects=(),
        )


@pytest.mark.asyncio
async def test_mcp_server_registers_surface_and_serves_tenant_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    profile_path = tmp_path / "profiles.yaml"
    profile_path.write_text(
        """catalog_name: mcp_test
profiles:
  - profile_id: ml
    name: ML Engineer
    target_roles: ["ML Engineer"]
    preferred_skills: ["ML"]
""",
        encoding="utf-8",
    )
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "tenant.json").write_text(
        json.dumps(
            {
                "tenant_id": "ai_jobs",
                "display_name": "AI Jobs",
                "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
                "store_backend": "sqlite",
                "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
                "job_group_store_backend": "sqlite",
                "job_backend": "sqlite",
                "search_backend": "sqlite",
                "filter_profile_path": str(profile_path),
                "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
            }
        ),
        encoding="utf-8",
    )

    class FakeMCP:
        def __init__(self, name: str) -> None:
            self.name = name
            self.tools: dict[str, object] = {}
            self.resources: dict[str, object] = {}

        def tool(self, func):  # type: ignore[no-untyped-def]
            self.tools[func.__name__] = func
            return func

        def resource(self, uri: str):  # type: ignore[no-untyped-def]
            def decorator(func):  # type: ignore[no-untyped-def]
                self.resources[uri] = func
                return func

            return decorator

        def run(self, **kwargs):  # type: ignore[no-untyped-def]
            self.run_kwargs = kwargs

    monkeypatch.setitem(sys.modules, "fastmcp", type("FastMCPModule", (), {"FastMCP": FakeMCP}))

    from job_ftch.adapters.mcp.server import create_server
    from job_ftch.application import tenant_runner as tenant_runner_module

    monkeypatch.setattr(
        tenant_runner_module,
        "build_llm",
        lambda settings: _AcceptingHeuristicLLMProvider(),
    )

    server = create_server(configs_dir=configs_dir)
    await server.startup()

    assert server.runner is not None
    server.runner.get_runtime("ai_jobs").llm_provider = _AcceptingHeuristicLLMProvider()

    assert server.app.name == "job_ftch"
    assert set(server.app.tools) == MCP_OPERATOR_TOOLS
    assert MCP_LEGACY_TOOLS.isdisjoint(server.app.tools)
    assert set(server.app.resources) == {
        "config://{tenant_id}",
        "jobs://{tenant_id}/latest",
        "jobs://{tenant_id}/run_summary",
    }

    run_summary = await server.app.tools["run_pipeline"]("ai_jobs")
    tenant_list = await server.app.tools["list_tenants"]()
    latest_jobs = json.loads(await server.app.resources["jobs://{tenant_id}/latest"]("ai_jobs"))
    pipeline_status = await server.app.tools["get_pipeline_status"]("ai_jobs")
    tenant_status = await server.app.tools["get_tenant_status"]("ai_jobs")
    sources_payload = await server.app.tools["get_sources"]("ai_jobs", True, True)
    prefilter_req = await server.app.tools["get_prefilter_requirements"](None)
    setup_reco = await server.app.tools["recommend_runtime_setup"](None, None, "mcp", None)
    setup_validation = await server.app.tools["validate_runtime_setup"]("mcp", None, None)
    bypass_caps = await server.app.tools["get_bypass_capabilities"]()
    saved_profile = await server.app.tools["save_profile"](
        "ai_jobs",
        "1",
        "ml",
        "machine learning engineer",
    )
    listed_profiles = await server.app.tools["list_profiles"]("ai_jobs", "1")
    active_profile = await server.app.tools["activate_profile"]("ai_jobs", "1", "ml")
    added_source = await server.app.tools["add_source"](
        "ai_jobs",
        "https://example.com/jobs",
        None,
        100,
    )
    listed_after_add = await server.app.tools["get_sources"]("ai_jobs", True, True)
    disabled_source = await server.app.tools["disable_source"](
        "ai_jobs",
        added_source["source_id"],
    )
    pipeline_runs = await server.app.tools["list_pipeline_runs"]("ai_jobs", 10)
    search_results = await server.app.tools["search_jobs"]("senior", "ai_jobs", 10)
    lineage_payload = await server.app.tools["get_job_lineage"](latest_jobs[0]["job_id"], "ai_jobs")
    pipeline_run = await server.app.tools["get_pipeline_run"](
        run_summary["source_run_id"], "ai_jobs"
    )

    assert run_summary["tenant_id"] == "ai_jobs"
    assert tenant_list[0]["tenant_id"] == "ai_jobs"
    assert latest_jobs[0]["source_name"] == "fixture"
    assert pipeline_status is not None
    assert pipeline_status["tenant_id"] == "ai_jobs"
    assert tenant_status["tenant_id"] == "ai_jobs"
    assert tenant_status["status"]["tenant_id"] == "ai_jobs"
    assert tenant_status["source_count"] >= 1
    assert "source_degradation" in tenant_status
    assert sources_payload["tenant_id"] == "ai_jobs"
    assert sources_payload["count"] >= 1
    assert any(item["source_id"] == "debug:fixture" for item in sources_payload["sources"])
    assert sources_payload["health"] is not None
    assert any(item.get("source_id") == "debug:fixture" for item in sources_payload["health"])
    assert prefilter_req["dataset_format"] == "jsonl"
    assert prefilter_req["size_requirements"]["recommended_total_rows"] == 2000
    assert prefilter_req["size_requirements"]["recommended_positive_rows"] == 150
    assert prefilter_req["promotion"]["require_eval_gate"] is True
    assert setup_reco["goal"] == "mcp"
    assert "commands" in setup_reco
    assert setup_validation["goal"] == "mcp"
    assert "checks" in setup_validation
    assert "capabilities" in bypass_caps or "status" in bypass_caps
    assert saved_profile["profile_id"] == "ml"
    assert listed_profiles[0]["active"] is True
    assert active_profile["profile_id"] == "ml"
    assert added_source["source_id"] == "career_site:example_com_jobs"
    assert any(
        item["source_id"] == "career_site:example_com_jobs" for item in listed_after_add["sources"]
    )
    assert disabled_source["status"] == "disabled"
    assert len(pipeline_runs) == 1
    assert pipeline_runs[0]["source_run_id"] == run_summary["source_run_id"]
    assert len(search_results) == 1
    assert lineage_payload is not None
    assert lineage_payload["job_id"] == latest_jobs[0]["job_id"]
    assert lineage_payload["source_run_id"] is not None
    assert pipeline_run is not None
    assert pipeline_run["source_run_id"] == run_summary["source_run_id"]

    await server.shutdown()


@pytest.mark.asyncio
async def test_mcp_server_real_fastmcp_tool_handlers(tmp_path: Path) -> None:
    pytest.importorskip("fastmcp")

    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "tenant.json").write_text(
        json.dumps(
            {
                "tenant_id": "ai_jobs",
                "display_name": "AI Jobs",
                "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
                "store_backend": "sqlite",
                "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
                "job_group_store_backend": "sqlite",
                "job_backend": "sqlite",
                "search_backend": "sqlite",
                "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
            }
        ),
        encoding="utf-8",
    )

    from job_ftch.adapters.mcp.server import create_server

    server = create_server(configs_dir=configs_dir)
    await server.startup()
    assert server.runner is not None

    groups = await server.runner.search_jobs("machine learning", tenant_id="ai_jobs", limit=5)
    assert isinstance(groups, list)

    job = await server.runner.get_job("nonexistent-id")
    assert job is None

    tenants = await server.runner.list_tenants()
    assert isinstance(tenants, list)
    assert len(tenants) > 0

    await server.shutdown()


def _install_fake_fastmcp(monkeypatch: pytest.MonkeyPatch) -> type:
    class FakeMCP:
        def __init__(self, name: str) -> None:
            self.name = name
            self.tools: dict[str, object] = {}
            self.resources: dict[str, object] = {}
            self.run_kwargs: dict[str, object] | None = None

        def tool(self, func):  # type: ignore[no-untyped-def]
            self.tools[func.__name__] = func
            return func

        def resource(self, uri: str):  # type: ignore[no-untyped-def]
            def decorator(func):  # type: ignore[no-untyped-def]
                self.resources[uri] = func
                return func

            return decorator

        def run(self, **kwargs):  # type: ignore[no-untyped-def]
            self.run_kwargs = kwargs

    monkeypatch.setitem(sys.modules, "fastmcp", type("FastMCPModule", (), {"FastMCP": FakeMCP}))
    return FakeMCP


def test_mcp_server_run_stdio_omits_host_port(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stdio must not forward host/port; FastMCP run_stdio_async rejects them."""
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "tenant.json").write_text(
        json.dumps({"tenant_id": "t1", "display_name": "T1", "sources": []}),
        encoding="utf-8",
    )
    _install_fake_fastmcp(monkeypatch)

    from job_ftch.adapters.mcp.server import create_server

    server = create_server(configs_dir=configs_dir)
    server.run(transport="stdio", host="0.0.0.0", port=9999)

    assert server.app.run_kwargs is not None
    assert server.app.run_kwargs == {"transport": "stdio"}
    assert "host" not in server.app.run_kwargs
    assert "port" not in server.app.run_kwargs


def test_mcp_server_run_http_forwards_host_port(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "tenant.json").write_text(
        json.dumps({"tenant_id": "t1", "display_name": "T1", "sources": []}),
        encoding="utf-8",
    )
    _install_fake_fastmcp(monkeypatch)

    from job_ftch.adapters.mcp.server import create_server

    server = create_server(configs_dir=configs_dir)
    server.run(transport="http", host="127.0.0.1", port=8123)

    assert server.app.run_kwargs is not None
    assert server.app.run_kwargs == {
        "transport": "http",
        "host": "127.0.0.1",
        "port": 8123,
    }


@pytest.mark.asyncio
async def test_mcp_clear_run_data_preserves_profiles_and_clears_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "tenant.json").write_text(
        json.dumps({"tenant_id": "t1", "display_name": "T1", "sources": []}),
        encoding="utf-8",
    )
    _install_fake_fastmcp(monkeypatch)

    from job_ftch.adapters.mcp.server import create_server

    output_path = tmp_path / "jobs.json"
    review_path = tmp_path / "review.jsonl"
    output_path.write_text("stale", encoding="utf-8")
    review_path.write_text("stale", encoding="utf-8")
    (tmp_path / "jobs.123.staging.jsonl").write_text("stale", encoding="utf-8")

    class StubRuntime:
        settings = SimpleNamespace(
            output_path=output_path,
            review_output_path=review_path,
            rejected_output_path=tmp_path / "rejected.jsonl",
            quarantine_output_path=tmp_path / "quarantine.jsonl",
        )

    class StubRunner:
        async def clear_run_data(self, tenant_id: str) -> dict[str, int]:
            assert tenant_id == "t1"
            return {"jobs": 2, "dedup_records": 3}

        def get_runtime(self, tenant_id: str) -> StubRuntime:
            assert tenant_id == "t1"
            return StubRuntime()

    server = create_server(configs_dir=configs_dir)
    server.runner = StubRunner()  # type: ignore[assignment]

    result = await server.app.tools["clear_run_data"]("t1", True)

    assert result == {"jobs": 2, "dedup_records": 3, "output_artifacts": 3}
    assert not output_path.exists()
    assert not review_path.exists()
    assert not (tmp_path / "jobs.123.staging.jsonl").exists()


def test_prepare_stdio_logging_keeps_stdout_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Application/structlog noise must not pollute MCP JSON-RPC on stdout."""
    from job_ftch.adapters.mcp.server import prepare_stdio_logging

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout_buf)
    monkeypatch.setattr(sys, "stderr", stderr_buf)

    # Simulate a process that already had a root handler on stdout (common default).
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(stdout_handler)
    root.setLevel(logging.INFO)

    prepare_stdio_logging("INFO")

    logging.getLogger("job_ftch.test.mcp_stdio").info("mcp-stdio-stdlib-line")
    structlog.get_logger("job_ftch.test.mcp_stdio").info(
        "mcp_stdio_structlog_line",
        probe=True,
    )

    stdout_text = stdout_buf.getvalue()
    stderr_text = stderr_buf.getvalue()

    assert "mcp-stdio-stdlib-line" not in stdout_text
    assert "mcp_stdio_structlog_line" not in stdout_text
    assert "mcp-stdio-stdlib-line" in stderr_text
    assert "mcp_stdio_structlog_line" in stderr_text


@pytest.mark.asyncio
async def test_mcp_run_pipeline_all_scope_propagates_operator_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "tenant.json").write_text(
        json.dumps({"tenant_id": "t1", "display_name": "T1", "sources": []}),
        encoding="utf-8",
    )
    _install_fake_fastmcp(monkeypatch)

    from job_ftch.adapters.mcp.server import create_server

    calls: list[dict[str, object]] = []

    class StubSummary:
        def as_dict(self) -> dict[str, object]:
            return {"tenant_id": "t1", "source_run_id": "run-1"}

    class StubRunner:
        async def run_all(
            self,
            *,
            concurrency: int = 4,
            max_items: int | None = None,
            user_id: str | None = None,
        ) -> list[StubSummary]:
            calls.append(
                {
                    "concurrency": concurrency,
                    "max_items": max_items,
                    "user_id": user_id,
                }
            )
            return [StubSummary()]

    server = create_server(configs_dir=configs_dir)
    server.runner = StubRunner()  # type: ignore[assignment]

    result = await server.app.tools["run_pipeline"](
        None,
        "operator-1",
        "all",
        None,
        7,
    )

    assert calls == [{"concurrency": 4, "max_items": 7, "user_id": "operator-1"}]
    assert result == [{"tenant_id": "t1", "source_run_id": "run-1"}]
    assert "requested_max_items" not in result[0]
    assert "requested_user_id" not in result[0]


def test_mcp_runtime_setup_browser_contract_uses_patchright(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import job_ftch.adapters.mcp.server as mcp_server

    monkeypatch.setattr(mcp_server, "_package_present", lambda name: name == "patchright")

    validation = mcp_server._validate_runtime_setup(goal="browser", inventory=None)
    recommendation = mcp_server._recommend_runtime_setup(
        goal="browser",
        platform=None,
        inventory=None,
        source_context=None,
    )
    payload = json.dumps({"validation": validation, "recommendation": recommendation}).lower()

    assert validation["ok"] is True
    assert not any(check["id"] == "package:playwright" for check in validation["checks"])
    assert any(check["id"] == "package:patchright" for check in validation["checks"])
    assert "playwright install" not in payload
    assert "patchright install chromium" in payload


def test_mcp_runtime_setup_missing_browser_recommends_patchright_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import job_ftch.adapters.mcp.server as mcp_server

    monkeypatch.setattr(mcp_server, "_package_present", lambda _name: False)

    recommendation = mcp_server._recommend_runtime_setup(
        goal="browser",
        platform=None,
        inventory=None,
        source_context=None,
    )

    assert recommendation["missing_extras"] == ["browser"]
    assert "uv sync --extra browser" in recommendation["commands"]
    assert "uv run patchright install chromium" in recommendation["commands"]
    assert "playwright install" not in json.dumps(recommendation).lower()


def test_mcp_server_cli_accepts_configs_dir_after_subcommand() -> None:
    """Documented ``job_ftch mcp-server --configs-dir ...`` must parse."""
    from job_ftch.cli import _build_parser

    args = _build_parser().parse_args(
        [
            "mcp-server",
            "--configs-dir",
            "config/tenants",
            "--transport",
            "stdio",
        ]
    )
    assert args.command == "mcp-server"
    assert args.configs_dir == "config/tenants"
    assert args.transport == "stdio"


def test_mcp_server_cli_accepts_configs_dir_before_subcommand() -> None:
    """Global ``--configs-dir`` before ``mcp-server`` must still win."""
    from job_ftch.cli import _build_parser

    args = _build_parser().parse_args(
        [
            "--configs-dir",
            "config/tenants",
            "mcp-server",
            "--transport",
            "http",
        ]
    )
    assert args.command == "mcp-server"
    assert args.configs_dir == "config/tenants"
    assert args.transport == "http"


def _offline_mcp_env(**overrides: str) -> dict[str, str]:
    """Env for offline MCP subprocess: heuristic LLM, no embeddings, sqlite-friendly."""
    import os

    env = {
        **os.environ,
        "JOB_FTCH_STORE_BACKEND": "sqlite",
        "JOB_FTCH_JOB_GROUP_STORE_BACKEND": "sqlite",
        "JOB_FTCH_JOB_BACKEND": "sqlite",
        "JOB_FTCH_SEARCH_BACKEND": "sqlite",
        "JOB_FTCH_EMBEDDING_ENABLED": "false",
        "JOB_FTCH_EMBEDDING_PREFILTER_ENABLED": "false",
        "JOB_FTCH_BGEM3_ENABLED": "false",
        "JOB_FTCH_RELEVANCE_BACKEND": "keywords",
        "JOB_FTCH_RELEVANCE_SHOT_BACKEND": "memory",
        "JOB_FTCH_LLM_BACKEND": "heuristic",
        "JOB_FTCH_LLM_RELEVANCE_MAX_PER_RUN": "0",
        "JOB_FTCH_LLM_PRESENTABLE_ENABLED": "false",
        "JOB_FTCH_ENV": "dev",
        "PYTHONUNBUFFERED": "1",
    }
    env.update(overrides)
    return env


def _write_offline_tenant_configs(tmp_path: Path) -> Path:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "tenant.json").write_text(
        json.dumps(
            {
                "tenant_id": "offline_t",
                "display_name": "Offline MCP",
                "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
                "store_backend": "sqlite",
                "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
                "job_group_store_backend": "sqlite",
                "job_backend": "sqlite",
                "search_backend": "sqlite",
                "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
            }
        ),
        encoding="utf-8",
    )
    return configs_dir


@pytest.mark.asyncio
async def test_mcp_stdio_e2e_offline_run_pipeline(tmp_path: Path) -> None:
    """Real stdio MCP: list tools + offline run_pipeline (no network).

    Catches stdout JSON-RPC pollution and host/port-on-stdio regressions because
    FastMCP Client fails to initialize when the subprocess protocol breaks.
    Cold tenant startup is intentionally slow (~20s); keep this smoke focused.
    """
    pytest.importorskip("fastmcp")
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    configs_dir = _write_offline_tenant_configs(tmp_path)
    transport = StdioTransport(
        command=sys.executable,
        args=[
            "-m",
            "job_ftch",
            "mcp-server",
            "--configs-dir",
            str(configs_dir),
            "--transport",
            "stdio",
        ],
        env=_offline_mcp_env(),
        cwd=str(Path.cwd()),
    )

    async with Client(transport, timeout=120, init_timeout=90) as client:
        tools = await client.list_tools()
        tool_names = {tool.name for tool in tools}
        assert MCP_OPERATOR_TOOLS.issubset(tool_names)
        assert MCP_LEGACY_TOOLS.isdisjoint(tool_names)
        assert "run_all_pipelines" not in tool_names

        tenants_result = await client.call_tool("list_tenants", {})
        assert tenants_result.is_error is False
        tenants = tenants_result.data
        assert isinstance(tenants, list)
        assert tenants[0]["tenant_id"] == "offline_t"

        run_result = await client.call_tool(
            "run_pipeline",
            {"tenant_id": "offline_t", "scope": "tenant"},
        )
        assert run_result.is_error is False
        summary = run_result.data
        assert isinstance(summary, dict)
        assert summary["tenant_id"] == "offline_t"
        assert summary["fetched"] >= 1
        assert summary["failed"] == 0
        assert summary.get("source_run_id")


@pytest.mark.asyncio
async def test_mcp_scenario_sources_and_tenant_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario: get_sources returns health/diagnostics; get_tenant_status degrades."""
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "tenant.json").write_text(
        json.dumps(
            {
                "tenant_id": "ai_jobs",
                "display_name": "AI Jobs",
                "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
                "store_backend": "sqlite",
                "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
                "job_group_store_backend": "sqlite",
                "job_backend": "sqlite",
                "search_backend": "sqlite",
                "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
            }
        ),
        encoding="utf-8",
    )
    _install_fake_fastmcp(monkeypatch)

    from job_ftch.adapters.mcp.server import create_server
    from job_ftch.application import tenant_runner as tenant_runner_module

    monkeypatch.setattr(
        tenant_runner_module,
        "build_llm",
        lambda settings: _AcceptingHeuristicLLMProvider(),
    )

    server = create_server(configs_dir=configs_dir)
    await server.startup()
    assert "list_sources" not in server.app.tools
    assert "list_source_health" not in server.app.tools
    assert "get_status" not in server.app.tools

    sources = await server.app.tools["get_sources"]("ai_jobs", True, True)
    tenant_status = await server.app.tools["get_tenant_status"]("ai_jobs")

    assert sources["tenant_id"] == "ai_jobs"
    assert sources["count"] >= 1
    assert isinstance(sources["sources"], list)
    first = sources["sources"][0]
    assert "source_id" in first
    assert sources["health"] is not None
    assert isinstance(sources["health"], list)
    # Diagnostics may be embedded on items and/or as a side payload depending on impl.
    # Diagnostics live on source items when include_diagnostics=True.
    assert sources["include_diagnostics"] is True
    assert sources["include_health"] is True

    assert tenant_status["tenant_id"] == "ai_jobs"
    assert "source_degradation" in tenant_status
    degradation = tenant_status["source_degradation"]
    assert isinstance(degradation, dict)
    assert "degraded_count" in degradation
    assert "failed_count" in degradation
    assert "by_status" in degradation
    assert degradation["total"] == sources["count"]

    await server.shutdown()


@pytest.mark.asyncio
async def test_mcp_scenario_pipeline_scope_tenant_and_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario: run_pipeline scope=tenant and scope=all; no run_all_pipelines tool."""
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "tenant.json").write_text(
        json.dumps(
            {
                "tenant_id": "offline_scope",
                "display_name": "Offline Scope",
                "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
                "store_backend": "sqlite",
                "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
                "job_group_store_backend": "sqlite",
                "job_backend": "sqlite",
                "search_backend": "sqlite",
                "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
            }
        ),
        encoding="utf-8",
    )
    _install_fake_fastmcp(monkeypatch)

    from job_ftch.adapters.mcp.server import create_server
    from job_ftch.application import tenant_runner as tenant_runner_module

    monkeypatch.setattr(
        tenant_runner_module,
        "build_llm",
        lambda settings: _AcceptingHeuristicLLMProvider(),
    )

    server = create_server(configs_dir=configs_dir)
    await server.startup()
    assert server.runner is not None
    server.runner.get_runtime("offline_scope").llm_provider = _AcceptingHeuristicLLMProvider()

    assert "run_all_pipelines" not in server.app.tools
    assert "run_pipeline" in server.app.tools

    tenant_run = await server.app.tools["run_pipeline"](
        "offline_scope",
        None,
        "tenant",
        None,
        None,
    )
    assert isinstance(tenant_run, dict)
    assert tenant_run["tenant_id"] == "offline_scope"
    assert tenant_run["fetched"] >= 1
    assert tenant_run.get("source_run_id")

    all_runs = await server.app.tools["run_pipeline"](None, None, "all", None, None)
    assert isinstance(all_runs, list)
    assert len(all_runs) >= 1
    assert any(item.get("tenant_id") == "offline_scope" for item in all_runs)

    await server.shutdown()


@pytest.mark.asyncio
async def test_mcp_scenario_setup_and_prefilter_shapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario: setup/prefilter tools return useful shapes without secrets."""
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "tenant.json").write_text(
        json.dumps({"tenant_id": "t_setup", "display_name": "Setup", "sources": []}),
        encoding="utf-8",
    )
    _install_fake_fastmcp(monkeypatch)

    from job_ftch.adapters.mcp.server import create_server

    server = create_server(configs_dir=configs_dir)
    await server.startup()

    protected = await server.app.tools["recommend_runtime_setup"](
        None,
        None,
        "protected_sites",
        None,
    )
    mcp_validation = await server.app.tools["validate_runtime_setup"]("mcp", None, None)
    prefilter = await server.app.tools["get_prefilter_requirements"](None)

    assert protected["goal"] == "protected_sites"
    assert isinstance(protected.get("commands"), list)
    assert isinstance(protected.get("missing_extras"), list)
    assert isinstance(protected.get("missing_env"), list)
    assert isinstance(protected.get("manual_steps"), list)
    assert isinstance(protected.get("warnings"), list)
    # Labels only, never secret values.
    for env_label in protected["missing_env"]:
        assert "sk-" not in str(env_label).lower()
        assert "bearer " not in str(env_label).lower()

    assert mcp_validation["goal"] == "mcp"
    assert isinstance(mcp_validation.get("checks"), list)
    assert "ok" in mcp_validation
    assert any(check.get("id") == "mcp_package" for check in mcp_validation["checks"])

    assert prefilter["dataset_format"] == "jsonl"
    assert "text" in prefilter["required_fields"]
    assert prefilter["size_requirements"]["recommended_total_rows"] >= 2000
    assert prefilter["size_requirements"]["recommended_positive_rows"] >= 150
    assert prefilter["promotion"]["require_eval_gate"] is True

    _assert_no_secret_values(protected)
    _assert_no_secret_values(mcp_validation)
    _assert_no_secret_values(prefilter)

    await server.shutdown()


@pytest.mark.asyncio
async def test_mcp_scenario_live_stdio_tool_list_operator_only(tmp_path: Path) -> None:
    """Scenario: live FastMCP stdio tool list has preferred names, no legacy."""
    pytest.importorskip("fastmcp")
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    configs_dir = _write_offline_tenant_configs(tmp_path)
    transport = StdioTransport(
        command=sys.executable,
        args=[
            "-m",
            "job_ftch",
            "mcp-server",
            "--configs-dir",
            str(configs_dir),
            "--transport",
            "stdio",
        ],
        env=_offline_mcp_env(),
        cwd=str(Path.cwd()),
    )

    async with Client(transport, timeout=120, init_timeout=90) as client:
        tools = await client.list_tools()
        tool_names = {tool.name for tool in tools}
        assert MCP_OPERATOR_TOOLS.issubset(tool_names)
        assert MCP_LEGACY_TOOLS.isdisjoint(tool_names)
        for legacy in sorted(MCP_LEGACY_TOOLS):
            assert legacy not in tool_names


@pytest.mark.asyncio
async def test_mcp_scenario_examples_resume_vacancy_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario: operator example tools map resume/vacancy × polarity."""
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "tenant.json").write_text(
        json.dumps({"tenant_id": "t_ex", "display_name": "Examples", "sources": []}),
        encoding="utf-8",
    )
    _install_fake_fastmcp(monkeypatch)

    from job_ftch.adapters.mcp.server import create_server

    server = create_server(configs_dir=configs_dir)
    await server.startup()

    added = await server.app.tools["add_example"](
        "t_ex",
        "u1",
        "vacancy",
        "positive",
        "Hiring senior LLM engineer, Python, RAG",
        None,
        "defer",
    )
    assert added["kind"] == "vacancy"
    assert added["label"] == "positive"
    assert added["prefilter_dirty"] is True
    assert added["counts"]["positive_vacancy"] == 1
    assert "positive_job" not in added["counts"]

    added_resume = await server.app.tools["add_example"](
        "t_ex",
        "u1",
        "resume",
        "negative",
        "Staff accountant with 1C only",
        None,
        "defer",
    )
    assert added_resume["counts"]["negative_resume"] == 1

    listed = await server.app.tools["list_examples"]("t_ex", "u1", None, "all", None)
    assert listed["examples"]["positive_vacancy"] == ["Hiring senior LLM engineer, Python, RAG"]
    assert listed["examples"]["negative_resume"] == ["Staff accountant with 1C only"]

    summary = await server.app.tools["get_examples_summary"]("t_ex", "u1", None)
    assert summary["total"] == 2

    bad = await server.app.tools["add_example"](
        "t_ex",
        "u1",
        "job",
        "positive",
        "legacy kind should fail",
        None,
        "auto",
    )
    assert bad["error"] == "invalid_arguments"

    cleared = await server.app.tools["clear_examples"]("t_ex", "u1", "vacancy", None)
    assert cleared["removed"] == 1
    remaining = await server.app.tools["get_examples_summary"]("t_ex", "u1", None)
    assert remaining["counts"]["positive_vacancy"] == 0
    assert remaining["counts"]["negative_resume"] == 1

    await server.shutdown()


@pytest.mark.asyncio
async def test_mcp_scenario_prefilter_prepare_validate_promote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario: prepare/validate/train dry-run/promote/rollback stay gated."""
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "tenant.json").write_text(
        json.dumps(
            {
                "tenant_id": "t_pf",
                "display_name": "Prefilter",
                "sources": [],
                "store_backend": "sqlite",
                "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            }
        ),
        encoding="utf-8",
    )
    _install_fake_fastmcp(monkeypatch)

    from job_ftch.adapters.mcp.server import create_server

    server = create_server(configs_dir=configs_dir)
    await server.startup()

    await server.app.tools["add_example"](
        "t_pf",
        "u1",
        "vacancy",
        "positive",
        "Hiring senior LLM engineer, Python, RAG",
        None,
        "defer",
    )
    await server.app.tools["add_example"](
        "t_pf",
        "u1",
        "vacancy",
        "negative",
        "Hiring salesperson for retail shop",
        None,
        "defer",
    )

    status = await server.app.tools["get_prefilter_status"]("t_pf", None)
    assert status["dirty"] is True
    assert status["promotion"]["automatic_after_example_change"] is False

    prepared = await server.app.tools["prepare_prefilter_dataset"](
        "t_pf",
        None,
        "examples",
        None,
        "u1",
    )
    assert prepared["n_rows"] == 2
    assert prepared["n_positive"] == 1
    assert prepared["ok"] is False

    validated = await server.app.tools["validate_prefilter_dataset"](
        prepared["path"],
        "t_pf",
    )
    assert validated["ok"] is False
    assert validated["production_ready"] is False

    dry = await server.app.tools["train_prefilter"](
        "t_pf",
        None,
        prepared["path"],
        True,
        0.30,
    )
    assert dry["dry_run"] is True
    assert dry["would_write"] is False

    root = tmp_path / "t_pf" / "prefilter" / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    (root / "art-ok.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_version": "tfidf-logreg-v1",
                "vocabulary": {},
                "idf": [],
                "coef": [],
                "intercept": 0.0,
                "training": {"n_rows": 2200, "n_positive": 250},
                "metrics": {
                    "target_threshold": 0.3,
                    "holdout_positive_retention": 0.95,
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "art-prev.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_version": "tfidf-logreg-v1",
                "vocabulary": {},
                "idf": [],
                "coef": [],
                "intercept": 0.0,
                "training": {"n_rows": 2200, "n_positive": 200},
                "metrics": {
                    "target_threshold": 0.3,
                    "holdout_positive_retention": 0.92,
                },
            }
        ),
        encoding="utf-8",
    )

    first = await server.app.tools["promote_prefilter"]("t_pf", "art-prev", None, True)
    assert first["ok"] is True
    second = await server.app.tools["promote_prefilter"]("t_pf", "art-ok", None, True)
    assert second["ok"] is True
    assert second["previous_artifact_id"] == "art-prev"
    live = await server.app.tools["get_prefilter_status"]("t_pf", None)
    assert live["using_promoted"] is True
    assert str(live["active_model_path"]).replace("\\", "/").endswith("prefilter/current.json")
    rolled = await server.app.tools["rollback_prefilter"]("t_pf", None)
    assert rolled["ok"] is True
    listed = await server.app.tools["list_prefilter_artifacts"]("t_pf", None)
    assert listed["count"] == 2
    assert listed["current_artifact_id"] == "art-prev"

    await server.shutdown()


@pytest.mark.asyncio
async def test_mcp_scenario_source_probe_and_browser_not_implemented(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario: cheap/full source probe, escalation, bypass diagnose, no live browser."""
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "tenant.json").write_text(
        json.dumps(
            {
                "tenant_id": "t_src",
                "display_name": "Source ops",
                "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
                "store_backend": "sqlite",
                "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
                "job_group_store_backend": "sqlite",
                "job_backend": "sqlite",
                "search_backend": "sqlite",
                "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
            }
        ),
        encoding="utf-8",
    )
    _install_fake_fastmcp(monkeypatch)

    from job_ftch.adapters.mcp.server import create_server
    from job_ftch.application import tenant_runner as tenant_runner_module

    monkeypatch.setattr(
        tenant_runner_module,
        "build_llm",
        lambda settings: _AcceptingHeuristicLLMProvider(),
    )

    server = create_server(configs_dir=configs_dir)
    await server.startup()

    listed = await server.app.tools["get_sources"]("t_src", True, True)
    assert listed["count"] >= 1
    source_id = str(listed["sources"][0]["source_id"])

    cheap = await server.app.tools["probe_source"]("t_src", source_id, "cheap", 5)
    assert cheap["ok"] is True
    assert cheap["executed"] is False
    assert cheap["status"] == "ok"
    assert cheap["source"]["source_id"] == source_id
    assert "route" in cheap
    assert cheap["selected_route"]["engine"] is not None
    _assert_no_secret_values(cheap)

    missing = await server.app.tools["probe_source"]("t_src", "debug:missing", "cheap", 5)
    assert missing["status"] == "source_not_found"
    assert missing["executed"] is False

    full = await server.app.tools["probe_source"]("t_src", source_id, "full", 2)
    assert full["executed"] is True
    assert full["run"]["tenant_id"] == "t_src"
    assert int(full["run"]["fetched"] or 0) >= 1

    listed_after = await server.app.tools["get_sources"]("t_src", True, True)
    source_id = str(listed_after["sources"][0]["source_id"])

    pinned = await server.app.tools["run_source"]("t_src", source_id, 1, None, "cloak")
    assert pinned["status"] == "unsupported"
    assert pinned["executed"] is False
    assert pinned["missing_service"] == "forced_bypass_override"
    assert "setup" in pinned

    recommended = await server.app.tools["run_source_escalation"](
        "t_src", source_id, "recommended", None, 2
    )
    assert recommended["status"] in {"ok", "empty", "degraded", "error"}
    assert isinstance(recommended["escalation_ladder"], list)

    swept = await server.app.tools["run_source_escalation"]("t_src", source_id, "all", None, 2)
    assert swept["status"] == "not_implemented"
    assert swept["executed"] is False
    assert swept["missing_service"] == "independent_bypass_sweep"
    assert "setup" in swept

    selected_engine = str(cheap["selected_route"]["engine"])
    bypass_ok = await server.app.tools["probe_bypass_route"]("t_src", source_id, selected_engine, 2)
    assert bypass_ok["executed"] is True
    assert bypass_ok["requested_bypass"] == selected_engine

    browser_bypass = await server.app.tools["probe_bypass_route"]("t_src", source_id, "cloak", 2)
    assert browser_bypass["status"] in {"not_implemented", "unavailable"}
    assert browser_bypass["executed"] is False
    assert "setup" in browser_bypass
    _assert_no_secret_values(browser_bypass)

    live = await server.app.tools["run_browser_probe"](
        "t_src", source_id, None, "listing", "auto", None, False, 5
    )
    assert live["status"] == "not_implemented"
    assert live["executed"] is False
    assert live["missing_service"] == "browser_session_probe"
    assert live["route"] is not None
    assert "setup" in live
    assert "commands" in live["setup"]
    _assert_no_secret_values(live)

    await server.shutdown()
