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

# Two-surface catalog. MCP_FORBIDDEN_TOOL_NAMES must stay unregistered.
MCP_SHARED_TOOLS = frozenset(
    {
        "list_tenants",
        "get_status",
        "get_runtime",
        "doctor",
        "get_sources",
        "update_source",
        "get_jobs",
        "update_shot",
    }
)
MCP_MASS_ONLY_TOOLS = frozenset(
    {
        "run_pipeline",
        "get_prefilter_status",
        "prepare_prefilter_dataset",
        "train_prefilter",
        "evaluate_prefilter",
        "promote_prefilter",
    }
)
MCP_PERSONAL_ONLY_TOOLS = frozenset(
    {
        "set_resume",
        "probe_page",
        "browser_session",
        "run_source",
    }
)
MCP_MASS_TOOLS = MCP_SHARED_TOOLS | MCP_MASS_ONLY_TOOLS
MCP_PERSONAL_TOOLS = MCP_SHARED_TOOLS | MCP_PERSONAL_ONLY_TOOLS
MCP_OPERATOR_TOOLS = MCP_SHARED_TOOLS | MCP_MASS_ONLY_TOOLS | MCP_PERSONAL_ONLY_TOOLS

MCP_FORBIDDEN_TOOL_NAMES = frozenset(
    {
        "run_all_pipelines",
        "list_source_health",
        "list_sources",
        "list_runs",
        "get_run",
        "list_browser_capabilities",
        "explain_browser_route",
        "plan_source_routes",
        "get_search_session_status",
        "list_search_results",
        "get_pipeline_status",
        "get_tenant_status",
        "list_pipeline_runs",
        "get_pipeline_run",
        "cancel_pipeline_run",
        "add_source",
        "disable_source",
        "remove_source",
        "add_example",
        "list_examples",
        "remove_example",
        "clear_examples",
        "get_examples_summary",
        "compile_examples_ontology",
        "ingest_resume",
        "list_profiles",
        "save_profile",
        "activate_profile",
        "search_jobs",
        "get_job",
        "get_job_lineage",
        "get_latest_jobs",
        "get_llm_backend_health",
        "get_bypass_capabilities",
        "get_bypass_routes",
        "recommend_bypass_route",
        "recommend_runtime_setup",
        "validate_runtime_setup",
        "probe_source",
        "probe_bypass_route",
        "run_source_escalation",
        "run_browser_probe",
        "open_browser_session",
        "get_browser_session",
        "continue_browser_session",
        "capture_browser_artifact",
        "close_browser_session",
        "explain_source_failure",
        "get_source_artifacts",
        "clear_run_data",
        "reset_tenant",
        "create_search_session",
        "plan_search_session",
        "approve_search_session",
        "run_search_session",
        "get_search_session",
        "list_search_session_results",
        "explain_search_session",
        "cancel_search_session",
        "list_feedback",
        "add_vacancy_feedback",
        "set_feedback_audience",
        "clear_feedback",
        "promote_feedback_to_example",
        "get_prefilter_requirements",
        "validate_prefilter_dataset",
        "list_prefilter_artifacts",
        "rollback_prefilter",
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
    monkeypatch.delenv("JOB_FTCH_MCP_SURFACE", raising=False)

    server = create_server(configs_dir=configs_dir)
    await server.startup()

    assert server.runner is not None
    server.runner.get_runtime("ai_jobs").llm_provider = _AcceptingHeuristicLLMProvider()

    assert server.app.name == "job_ftch"
    assert set(server.app.tools) == MCP_OPERATOR_TOOLS
    assert len(server.app.tools) == 18
    assert MCP_FORBIDDEN_TOOL_NAMES.isdisjoint(server.app.tools)
    assert set(server.app.resources) == {"config://{tenant_id}"}

    run_summary = await server.app.tools["run_pipeline"](tenant_id="ai_jobs")
    tenant_list = await server.app.tools["list_tenants"]()
    latest_jobs = await server.app.tools["get_jobs"](tenant_id="ai_jobs", limit=10)
    tenant_status = await server.app.tools["get_status"]("ai_jobs")
    sources_payload = await server.app.tools["get_sources"]("ai_jobs")
    prefilter_status = await server.app.tools["get_prefilter_status"]("ai_jobs", None)
    runtime = await server.app.tools["get_runtime"]()
    added_source = await server.app.tools["update_source"](
        tenant_id="ai_jobs",
        action="add",
        link="https://example.com/jobs",
        source_type=None,
        limit=100,
    )
    listed_after_add = await server.app.tools["get_sources"]("ai_jobs")
    disabled_source = await server.app.tools["update_source"](
        tenant_id="ai_jobs",
        action="update",
        source_id=added_source["source_id"],
        enabled=False,
    )
    search_results = await server.app.tools["get_jobs"](
        tenant_id="ai_jobs",
        query="senior",
        limit=10,
    )
    assert all("title" in item for item in search_results["jobs"])
    first_job = latest_jobs["jobs"][0]
    lineage_payload = await server.app.tools["get_jobs"](
        tenant_id="ai_jobs",
        job_id=first_job["job_id"],
        include_lineage=True,
    )
    pipeline_run = await server.app.tools["get_status"](
        "ai_jobs",
        run_summary["source_run_id"],
    )

    assert run_summary["tenant_id"] == "ai_jobs"
    assert tenant_list[0]["tenant_id"] == "ai_jobs"
    assert first_job["source_name"] == "fixture"
    assert tenant_status["tenant_id"] == "ai_jobs"
    assert tenant_status["status"]["tenant_id"] == "ai_jobs"
    assert tenant_status["source_count"] >= 1
    assert "source_degradation" in tenant_status
    assert tenant_status["recent_runs"][0]["source_run_id"] == run_summary["source_run_id"]
    assert sources_payload["tenant_id"] == "ai_jobs"
    assert sources_payload["count"] >= 1
    assert any(item["source_id"] == "debug:fixture" for item in sources_payload["sources"])
    assert sources_payload["health"] is not None
    assert any(item.get("source_id") == "debug:fixture" for item in sources_payload["health"])
    prefilter_req = prefilter_status["requirements"]
    assert prefilter_req["dataset_format"] == "jsonl"
    assert prefilter_req["size_requirements"]["recommended_total_rows"] == 2000
    assert prefilter_req["size_requirements"]["recommended_positive_rows"] == 150
    assert prefilter_req["promotion"]["require_eval_gate"] is True
    assert "engines" in runtime
    assert "llm" in runtime
    assert "residential_proxies" in runtime
    assert "captcha_solvers" in runtime
    assert added_source["source_id"] == "career_site:example_com_jobs"
    assert any(
        item["source_id"] == "career_site:example_com_jobs" for item in listed_after_add["sources"]
    )
    assert disabled_source["status"] == "disabled"
    assert search_results["count"] == 1
    assert lineage_payload["lineage"] is not None
    assert lineage_payload["lineage"]["job_id"] == first_job["job_id"]
    assert lineage_payload["lineage"]["source_run_id"] is not None
    assert pipeline_run["run"] is not None
    assert pipeline_run["run"]["source_run_id"] == run_summary["source_run_id"]

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

    class StubSummary:
        def as_dict(self) -> dict[str, object]:
            return {"tenant_id": "t1", "source_run_id": "run-1"}

    class StubRunner:
        async def clear_run_data(self, tenant_id: str) -> dict[str, int]:
            assert tenant_id == "t1"
            return {"jobs": 2, "dedup_records": 3}

        async def run_tenant(
            self,
            tenant_id: str,
            max_items: int | None = None,
            user_id: str | None = None,
            source_ids: list[str] | None = None,
        ) -> StubSummary:
            del max_items, user_id, source_ids
            assert tenant_id == "t1"
            return StubSummary()

        def get_runtime(self, tenant_id: str) -> StubRuntime:
            assert tenant_id == "t1"
            return StubRuntime()

    server = create_server(configs_dir=configs_dir)
    server.runner = StubRunner()  # type: ignore[assignment]

    result = await server.app.tools["run_pipeline"](tenant_id="t1", clear_first=True)

    assert result["tenant_id"] == "t1"
    assert result["cleared"] == {"jobs": 2, "dedup_records": 3, "output_artifacts": 3}
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
        tenant_id=None,
        source_ids=None,
        max_items=7,
        clear_first=False,
        user_id="operator-1",
        scope="all",
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
        "JOB_FTCH_MCP_SURFACE": "all",
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
        assert tool_names == MCP_OPERATOR_TOOLS
        assert MCP_FORBIDDEN_TOOL_NAMES.isdisjoint(tool_names)
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
    """Scenario: get_sources returns health/diagnostics; get_status degrades."""
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
    assert "get_tenant_status" not in server.app.tools
    assert "get_status" in server.app.tools

    sources = await server.app.tools["get_sources"]("ai_jobs")
    tenant_status = await server.app.tools["get_status"]("ai_jobs")

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
        tenant_id="offline_scope",
        scope="tenant",
    )
    assert isinstance(tenant_run, dict)
    assert tenant_run["tenant_id"] == "offline_scope"
    assert tenant_run["fetched"] >= 1
    assert tenant_run.get("source_run_id")

    all_runs = await server.app.tools["run_pipeline"](scope="all")
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

    runtime = await server.app.tools["get_runtime"]()
    prefilter = await server.app.tools["get_prefilter_status"]("t_setup", None)

    assert set(runtime["engines"]) == {
        "stealth_browser",
        "playwright",
        "patchright",
        "nodriver",
        "camoufox",
        "cloak",
    }
    assert "llm" in runtime
    assert runtime["residential_proxies"]["configured"] in {True, False}
    assert any(item["id"] == "browser_wait" for item in runtime["captcha_solvers"])
    assert "install_hints" in runtime
    _assert_no_secret_values(runtime)

    requirements = prefilter["requirements"]
    assert requirements["dataset_format"] == "jsonl"
    assert "text" in requirements["required_fields"]
    assert requirements["size_requirements"]["recommended_total_rows"] >= 2000
    assert requirements["size_requirements"]["recommended_positive_rows"] >= 150
    assert requirements["promotion"]["require_eval_gate"] is True
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
        assert tool_names == MCP_OPERATOR_TOOLS
        assert MCP_FORBIDDEN_TOOL_NAMES.isdisjoint(tool_names)
        for legacy in sorted(MCP_FORBIDDEN_TOOL_NAMES):
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

    added = await server.app.tools["update_shot"](
        tenant_id="t_ex",
        user_id="u1",
        action="add",
        kind="vacancy",
        label="positive",
        text="Hiring senior LLM engineer, Python, RAG",
    )
    assert added["kind"] == "vacancy"
    assert added["label"] == "positive"
    assert added["prefilter_dirty"] is True
    assert added["counts"]["positive_vacancy"] == 1
    assert "positive_job" not in added["counts"]

    added_resume = await server.app.tools["update_shot"](
        tenant_id="t_ex",
        user_id="u1",
        action="add",
        kind="resume",
        label="negative",
        text="Staff accountant with 1C only",
    )
    assert added_resume["counts"]["negative_resume"] == 1

    listed = await server.app.tools["update_shot"](
        tenant_id="t_ex",
        user_id="u1",
        action="list",
    )
    assert listed["examples"]["positive_vacancy"] == ["Hiring senior LLM engineer, Python, RAG"]
    assert listed["examples"]["negative_resume"] == ["Staff accountant with 1C only"]
    assert listed["counts"]["positive_vacancy"] + listed["counts"]["negative_resume"] == 2

    removed = await server.app.tools["update_shot"](
        tenant_id="t_ex",
        user_id="u1",
        action="remove",
        kind="vacancy",
        label="positive",
        index=0,
    )
    assert removed["removed_index"] == 0
    after_remove = await server.app.tools["update_shot"](
        tenant_id="t_ex",
        user_id="u1",
        action="list",
    )
    assert after_remove["counts"].get("positive_vacancy", 0) == 0
    assert after_remove["examples"]["negative_resume"] == ["Staff accountant with 1C only"]

    missing_kind = await server.app.tools["update_shot"](
        tenant_id="t_ex",
        user_id="u1",
        action="add",
        label="positive",
        text="needs kind",
    )
    assert missing_kind["error"] == "invalid_arguments"
    missing_label = await server.app.tools["update_shot"](
        tenant_id="t_ex",
        user_id="u1",
        action="remove",
        kind="resume",
        index=0,
    )
    assert missing_label["error"] == "invalid_arguments"

    bad = await server.app.tools["update_shot"](
        tenant_id="t_ex",
        user_id="u1",
        action="add",
        kind="job",
        label="positive",
        text="legacy kind should fail",
    )
    assert bad["error"] == "invalid_arguments"

    await server.app.tools["update_shot"](
        tenant_id="t_ex",
        user_id="u1",
        action="add",
        kind="vacancy",
        label="positive",
        text="Hiring senior LLM engineer, Python, RAG",
    )
    cleared = await server.app.tools["update_shot"](
        tenant_id="t_ex",
        user_id="u1",
        action="clear",
        kind="vacancy",
    )
    assert cleared["removed"] == 1
    remaining = await server.app.tools["update_shot"](
        tenant_id="t_ex",
        user_id="u1",
        action="list",
    )
    assert remaining["counts"].get("positive_vacancy", 0) == 0
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

    await server.app.tools["update_shot"](
        tenant_id="t_pf",
        user_id="u1",
        action="add",
        kind="vacancy",
        label="positive",
        text="Hiring senior LLM engineer, Python, RAG",
    )
    await server.app.tools["update_shot"](
        tenant_id="t_pf",
        user_id="u1",
        action="add",
        kind="vacancy",
        label="negative",
        text="Hiring salesperson for retail shop",
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

    evaluated = await server.app.tools["evaluate_prefilter"]("t_pf", "art-ok")
    assert evaluated["ok"] is True
    assert evaluated["gate_pass"] is True
    assert evaluated["stored_metrics"]["holdout_positive_retention"] == 0.95
    assert evaluated["min_holdout_retention"] == 0.90
    assert evaluated["artifact_id"] == "art-ok"

    first = await server.app.tools["promote_prefilter"]("t_pf", "art-prev", None, True, False)
    assert first["ok"] is True
    second = await server.app.tools["promote_prefilter"]("t_pf", "art-ok", None, True, False)
    assert second["ok"] is True
    assert second["previous_artifact_id"] == "art-prev"
    live = await server.app.tools["get_prefilter_status"]("t_pf", None)
    assert live["using_promoted"] is True
    assert str(live["active_model_path"]).replace("\\", "/").endswith("prefilter/current.json")
    rolled = await server.app.tools["promote_prefilter"](
        tenant_id="t_pf",
        rollback=True,
    )
    assert rolled["ok"] is True
    listed = await server.app.tools["get_prefilter_status"]("t_pf", None)
    assert listed["artifact_count"] == 2
    assert listed["current_artifact_id"] == "art-prev"

    await server.shutdown()


@pytest.mark.asyncio
async def test_mcp_scenario_source_probe_pin_and_listing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario: cheap/full probe, bypass pin, bounded sweep, listing-url required."""
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

    listed = await server.app.tools["get_sources"]("t_src")
    assert listed["count"] >= 1
    source_id = str(listed["sources"][0]["source_id"])

    adaptive = await server.app.tools["run_source"](
        tenant_id="t_src",
        source_id=source_id,
        max_items=2,
    )
    assert adaptive["status"] in {"ok", "empty", "degraded", "error"}
    assert adaptive["escalation"] == "adaptive"

    pinned = await server.app.tools["run_source"](
        tenant_id="t_src",
        source_id=source_id,
        bypass="cloak",
        max_items=1,
    )
    assert pinned["status"] in {"ok", "empty", "degraded", "error", "unavailable"}
    assert pinned["requested_bypass"] == "cloak"
    assert "parse" in pinned or pinned["status"] == "unavailable"
    assert "setup" in pinned

    swept = await server.app.tools["run_source"](
        tenant_id="t_src",
        source_id=source_id,
        escalation="all",
        max_items=2,
        max_tier="noop",
    )
    assert swept["status"] in {"ok", "degraded", "unavailable", "unsupported"}
    assert swept.get("strategy") == "all" or swept.get("escalation") == "all"
    assert isinstance(swept.get("attempts"), list)
    if swept.get("escalation_ladder"):
        assert swept["escalation_ladder"][0] == "noop"
    if swept.get("attempts"):
        assert all("parse" in item and "stage" in item["parse"] for item in swept["attempts"])
    if "setup" in swept:
        assert "commands" in swept["setup"]

    detached = await server.app.tools["run_source"](
        tenant_id="t_src",
        source_id=source_id,
        session_id="sess-1",
    )
    assert detached["error"] == "session_not_found"
    assert detached.get("session_attached") is False
    assert detached["executed"] is False

    combo = await server.app.tools["run_source"](
        tenant_id="t_src",
        source_id=source_id,
        session_id="sess-1",
        escalation="all",
        max_tier="noop",
    )
    assert combo.get("error") != "invalid_arguments"
    assert combo.get("strategy") == "all" or combo.get("escalation") == "all"

    from job_ftch.infrastructure.browser_session import (
        OperatorBrowserSessionService,
        _LiveSession,
    )

    dummy_page = object()
    live_session = _LiveSession(
        tenant_id="t_src",
        url="https://example.com/jobs",
        engine="auto",
        headed=False,
        bypass_config=None,
        manual_challenge=False,
    )
    live_session.page = dummy_page
    sessions = OperatorBrowserSessionService()
    sessions._sessions[live_session.id] = live_session
    assert server.runner is not None
    server.runner._operator_sessions = sessions
    attached = await server.app.tools["run_source"](
        tenant_id="t_src",
        source_id=source_id,
        session_id=live_session.id,
        max_items=2,
    )
    assert attached["session_attached"] is True
    assert attached["session_id"] == live_session.id
    assert attached["error"] != "session_not_attached_to_ingest"

    live = await server.app.tools["probe_page"](
        tenant_id="t_src",
        source_id=source_id,
        what="listing",
    )
    assert live["status"] == "unsupported"
    assert live["executed"] is False
    assert live["error"] == "listing_url_required"
    assert live["ingest"] is False
    assert live["route"] is not None
    assert "setup" in live
    assert "commands" in live["setup"]
    _assert_no_secret_values(live)

    detail = await server.app.tools["probe_page"](
        tenant_id="t_src",
        source_id=source_id,
        what="detail",
        max_items=1,
    )
    assert detail["status"] == "unsupported"
    assert detail["error"] == "listing_url_required"
    _assert_no_secret_values(detail)

    challenge = await server.app.tools["probe_page"](
        tenant_id="t_src",
        source_id=source_id,
        what="challenge",
    )
    assert challenge["status"] == "unsupported"
    assert challenge["error"] == "listing_url_required"
    assert challenge["ingest"] is False
    _assert_no_secret_values(challenge)

    bad_what = await server.app.tools["probe_page"](
        tenant_id="t_src",
        source_id=source_id,
        what="custom_safe",
    )
    assert bad_what["error"] == "invalid_arguments"

    playwright_probe = await server.app.tools["probe_page"](
        tenant_id="t_src",
        source_id=source_id,
        what="listing",
        engine="playwright",
    )
    assert playwright_probe.get("error") != "unsupported_engine"

    async def _fake_fingerprint_probe(**kwargs: object) -> dict[str, object]:
        del kwargs
        return {
            "ok": True,
            "status": "ok",
            "executed": True,
            "engine": "patchright_browser",
            "fingerprint": {
                "site_class": "SSR",
                "recommended_monitors": ["dom"],
                "detected": {"render": False, "challenge": False},
            },
            "notes": ["fingerprint probe uses HTTP site classification"],
        }

    monkeypatch.setattr(
        "job_ftch.infrastructure.browser_probe.probe_fingerprint",
        _fake_fingerprint_probe,
    )
    fingerprint = await server.app.tools["probe_page"](
        tenant_id="t_src",
        source_id=source_id,
        url="https://example.com/jobs",
        what="fingerprint",
        max_items=1,
    )
    assert fingerprint["status"] != "not_implemented"
    assert fingerprint["executed"] is True
    assert fingerprint["fingerprint"]["site_class"] == "SSR"
    assert "secret" not in str(fingerprint.get("fingerprint"))
    _assert_no_secret_values(fingerprint)

    session = await server.app.tools["browser_session"](
        action="open",
        tenant_id="t_src",
        source_id=source_id,
        engine="auto",
        headed=False,
        profile="ephemeral",
    )
    assert session["status"] == "unsupported"
    assert session["error"] == "listing_url_required"
    _assert_no_secret_values(session)

    pinned_parser = await server.app.tools["run_source"](
        tenant_id="t_src",
        source_id=source_id,
        parser="generic",
        max_items=1,
    )
    assert pinned_parser["status"] == "unsupported"
    assert pinned_parser["error"] == "parser_pin_unsupported_source"
    assert pinned_parser["requested_parser"] == "generic"
    _assert_no_secret_values(pinned_parser)

    await server.shutdown()


@pytest.mark.asyncio
async def test_mcp_operator_remaining_surface(
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
    server.runner.get_runtime("ai_jobs").llm_provider = _AcceptingHeuristicLLMProvider()

    assert MCP_FORBIDDEN_TOOL_NAMES.isdisjoint(server.app.tools)
    assert set(server.app.tools) == MCP_OPERATOR_TOOLS

    run = await server.app.tools["run_pipeline"](tenant_id="ai_jobs")
    assert run["fetched"] >= 1
    latest = await server.app.tools["get_jobs"](tenant_id="ai_jobs", limit=10)
    assert latest["count"] >= 1
    job_id = latest["jobs"][0]["job_id"]
    one = await server.app.tools["get_jobs"](tenant_id="ai_jobs", job_id=job_id)
    assert one["job"]["job_id"] == job_id

    compiled = await server.app.tools["update_shot"](
        tenant_id="ai_jobs",
        user_id="op",
        action="compile",
        dry_run=True,
    )
    assert compiled["dry_run"] is True
    assert compiled["persisted"] is False
    assert isinstance(compiled.get("ontology_errors"), list)

    resume = await server.app.tools["set_resume"](
        tenant_id="ai_jobs",
        user_id="op",
        resume_text="Senior ML engineer, Python, LLM",
    )
    assert resume["profile_id"]
    assert resume["prefilter_dirty"] is True

    sources_payload = await server.app.tools["get_sources"]("ai_jobs")
    config_source_id = next(
        item["source_id"] for item in sources_payload["sources"] if item.get("origin") == "config"
    )
    added_source = await server.app.tools["update_source"](
        tenant_id="ai_jobs",
        action="add",
        link="https://example.com/jobs",
        limit=100,
    )
    runtime_id = added_source["source_id"]
    patched = await server.app.tools["update_source"](
        tenant_id="ai_jobs",
        action="update",
        source_id=runtime_id,
        enabled=False,
        limit=5,
    )
    assert patched["status"] == "disabled"
    assert patched.get("enabled") is False
    assert patched.get("source_id") == runtime_id
    spec = patched.get("spec") or {}
    assert spec.get("limit") == 5
    assert patched.get("limit") == 5
    assert isinstance(patched.get("source"), dict)
    config_limit = await server.app.tools["update_source"](
        tenant_id="ai_jobs",
        action="update",
        source_id=config_source_id,
        limit=3,
    )
    assert config_limit["status"] == "unsupported"
    assert config_limit["error"] == "config_limit_not_updatable"
    config_disabled = await server.app.tools["update_source"](
        tenant_id="ai_jobs",
        action="update",
        source_id=config_source_id,
        enabled=False,
    )
    assert config_disabled["status"] == "disabled"
    assert config_disabled.get("enabled") is False
    assert isinstance(config_disabled.get("source"), dict)
    removed = await server.app.tools["update_source"](
        tenant_id="ai_jobs",
        action="remove",
        source_id=runtime_id,
    )
    assert removed["status"] == "removed"
    base = await server.app.tools["update_source"](
        tenant_id="ai_jobs",
        action="remove",
        source_id=config_source_id,
    )
    assert base["status"] == "unsupported"
    assert base["error"] == "config_source_not_deletable"

    await server.shutdown()


@pytest.mark.asyncio
async def test_mcp_browser_session_dispatcher_forwards_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "tenant.json").write_text(
        json.dumps({"tenant_id": "t_sess", "display_name": "Sessions", "sources": []}),
        encoding="utf-8",
    )
    _install_fake_fastmcp(monkeypatch)
    from job_ftch.adapters.mcp.server import create_server

    server = create_server(configs_dir=configs_dir)
    await server.startup()

    bad_action = await server.app.tools["browser_session"](action="reload")
    assert bad_action["error"] == "invalid_arguments"

    open_missing = await server.app.tools["browser_session"](action="open")
    assert open_missing["error"] == "invalid_arguments"

    for action in ("status", "wait", "solve", "goto", "capture", "close"):
        missing = await server.app.tools["browser_session"](action=action)
        assert missing["error"] == "invalid_arguments"

    goto_no_url = await server.app.tools["browser_session"](
        action="goto",
        session_id="sess-1",
    )
    assert goto_no_url["error"] == "invalid_arguments"

    seen: list[tuple[str, str | None]] = []

    async def _get(runner: object, *, session_id: str) -> dict[str, object]:
        del runner
        seen.append(("get", session_id))
        return {"ok": True, "action": "status", "session_id": session_id}

    async def _continue(
        runner: object,
        *,
        session_id: str,
        instruction: str | None = None,
    ) -> dict[str, object]:
        del runner
        seen.append(("continue", instruction))
        return {"ok": True, "action": "continue", "session_id": session_id, "instruction": instruction}

    async def _capture(
        runner: object,
        *,
        session_id: str,
        artifact_type: str = "text",
    ) -> dict[str, object]:
        del runner
        seen.append(("capture", artifact_type))
        return {"ok": True, "action": "capture", "session_id": session_id, "artifact_type": artifact_type}

    async def _close(runner: object, *, session_id: str) -> dict[str, object]:
        del runner
        seen.append(("close", session_id))
        return {"ok": True, "action": "close", "session_id": session_id}

    monkeypatch.setattr(
        "job_ftch.application.source_operations.get_browser_session",
        _get,
    )
    monkeypatch.setattr(
        "job_ftch.application.source_operations.continue_browser_session",
        _continue,
    )
    monkeypatch.setattr(
        "job_ftch.application.source_operations.capture_browser_artifact",
        _capture,
    )
    monkeypatch.setattr(
        "job_ftch.application.source_operations.close_browser_session",
        _close,
    )

    status = await server.app.tools["browser_session"](action="status", session_id="sess-1")
    assert status["action"] == "status"
    wait = await server.app.tools["browser_session"](action="wait", session_id="sess-1")
    assert wait["instruction"] == "wait"
    solve = await server.app.tools["browser_session"](action="solve", session_id="sess-1")
    assert solve["instruction"] == "solve"
    solve_provider = await server.app.tools["browser_session"](
        action="solve",
        session_id="sess-1",
        solve="provider",
    )
    assert solve_provider["instruction"] == "solve:provider"
    goto = await server.app.tools["browser_session"](
        action="goto",
        session_id="sess-1",
        url="https://example.com/jobs",
    )
    assert goto["instruction"] == "navigate https://example.com/jobs"
    captured = await server.app.tools["browser_session"](action="capture", session_id="sess-1")
    assert captured["action"] == "capture"
    closed = await server.app.tools["browser_session"](action="close", session_id="sess-1")
    assert closed["action"] == "close"
    assert ("get", "sess-1") in seen
    assert ("continue", "wait") in seen
    assert ("continue", "solve") in seen
    assert ("continue", "solve:provider") in seen
    assert ("continue", "navigate https://example.com/jobs") in seen
    assert ("capture", "text") in seen
    assert ("close", "sess-1") in seen
    await server.shutdown()


def test_mcp_surface_tool_sets() -> None:
    assert "doctor" in MCP_SHARED_TOOLS
    assert len(MCP_SHARED_TOOLS) == 8
    assert len(MCP_MASS_TOOLS) == 14
    assert len(MCP_PERSONAL_TOOLS) == 12
    assert len(MCP_OPERATOR_TOOLS) == 18
    assert MCP_MASS_ONLY_TOOLS.isdisjoint(MCP_PERSONAL_ONLY_TOOLS)
    assert MCP_SHARED_TOOLS <= MCP_MASS_TOOLS
    assert MCP_SHARED_TOOLS <= MCP_PERSONAL_TOOLS
    assert MCP_FORBIDDEN_TOOL_NAMES.isdisjoint(MCP_OPERATOR_TOOLS)


@pytest.mark.asyncio
async def test_mcp_mass_surface_excludes_personal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JOB_FTCH_MCP_SURFACE", "mass")
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "tenant.json").write_text(
        json.dumps(
            {
                "tenant_id": "t1",
                "display_name": "T1",
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
    assert set(server.app.tools) == MCP_MASS_TOOLS
    assert MCP_PERSONAL_ONLY_TOOLS.isdisjoint(server.app.tools)
    assert MCP_FORBIDDEN_TOOL_NAMES.isdisjoint(server.app.tools)
    assert "doctor" in server.app.tools
    assert "set_resume" not in server.app.tools
    assert "probe_page" not in server.app.tools
    assert "run_source" not in server.app.tools

    added = await server.app.tools["update_shot"](
        tenant_id="t1",
        user_id="u1",
        action="add",
        kind="vacancy",
        label="positive",
        text="Hiring senior LLM engineer",
    )
    assert added["added"] == 1
    listed = await server.app.tools["update_shot"](tenant_id="t1", user_id="u1", action="list")
    assert listed["counts"]["positive_vacancy"] == 1
    ran = await server.app.tools["run_pipeline"](tenant_id="t1")
    assert isinstance(ran, dict)
    assert ran.get("tenant_id") == "t1"
    await server.shutdown()


@pytest.mark.asyncio
async def test_mcp_personal_surface_excludes_mass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JOB_FTCH_MCP_SURFACE", "personal")
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "tenant.json").write_text(
        json.dumps(
            {
                "tenant_id": "t1",
                "display_name": "T1",
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
    assert set(server.app.tools) == MCP_PERSONAL_TOOLS
    assert MCP_MASS_ONLY_TOOLS.isdisjoint(server.app.tools)
    assert MCP_FORBIDDEN_TOOL_NAMES.isdisjoint(server.app.tools)
    assert "doctor" in server.app.tools
    assert "run_pipeline" not in server.app.tools
    assert "promote_prefilter" not in server.app.tools

    ingested = await server.app.tools["set_resume"](
        tenant_id="t1",
        user_id="u1",
        resume_text="Senior ML engineer, Python, LLM",
    )
    assert ingested["profile_id"]
    assert ingested["prefilter_dirty"] is True
    await server.shutdown()


@pytest.mark.asyncio
async def test_mcp_get_runtime_probes_llm_residential_captcha(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "JOB_FTCH_RESIDENTIAL_PROXY_LIST",
        "http://user:s3cret@10.9.8.7:8080",  # pragma: allowlist secret
    )
    monkeypatch.setenv("CAPSOLVER_API_KEY", "cap-secret-value")  # pragma: allowlist secret
    monkeypatch.delenv("CAPMONSTER_API_KEY", raising=False)
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "tenant.json").write_text(
        json.dumps({"tenant_id": "t_rt", "display_name": "Runtime", "sources": []}),
        encoding="utf-8",
    )
    _install_fake_fastmcp(monkeypatch)

    class _Resp:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {"data": [{"id": "gpt-5.4-mini"}]}

    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.kwargs = kwargs

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str, **kwargs: object) -> _Resp:
            del url, kwargs
            return _Resp()

    monkeypatch.setattr("httpx.AsyncClient", _Client)
    from job_ftch.adapters.mcp.server import create_server
    from job_ftch.config import Settings

    settings = Settings(
        llm_backend="openai",
        openai_api_key="sk-live-secret",  # type: ignore[arg-type]
        openai_base_url="http://127.0.0.1:8317/v1",
        openai_model="gpt-5.4-mini",
        captcha_provider="capsolver",
        captcha_enabled_providers=["browser_wait", "capsolver"],
        store_backend="sqlite",
        job_backend="sqlite",
        search_backend="sqlite",
        job_group_store_backend="sqlite",
    )
    server = create_server(configs_dir=configs_dir, base_settings=settings)
    await server.startup()
    payload = await server.app.tools["get_runtime"]()
    blob = json.dumps(payload)
    assert payload["llm"]["ok"] is True
    assert payload["llm"]["reachable"] is True
    assert payload["residential_proxies"]["configured"] is True
    assert payload["residential_proxies"]["reachable"] is True
    solvers = {item["id"]: item for item in payload["captcha_solvers"]}
    assert solvers["browser_wait"]["key_present"] is True
    assert solvers["capsolver"]["key_present"] is True
    assert "s3cret" not in blob
    assert "10.9.8.7" not in blob
    assert "cap-secret-value" not in blob
    assert "sk-live-secret" not in blob
    assert "http://user:" not in blob
    _assert_no_secret_values(payload)
    diagnosis = await server.app.tools["doctor"]()
    doctor_blob = json.dumps(diagnosis)
    assert "s3cret" not in diagnosis["report"]
    assert "s3cret" not in doctor_blob
    assert "10.9.8.7" not in diagnosis["report"]
    assert "10.9.8.7" not in doctor_blob
    assert "http://user:" not in doctor_blob
    assert "cap-secret-value" not in doctor_blob
    assert "sk-live-secret" not in doctor_blob
    _assert_no_secret_values(diagnosis)
    await server.shutdown()


@pytest.mark.asyncio
async def test_mcp_get_runtime_empty_residential_and_llm_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JOB_FTCH_RESIDENTIAL_PROXY_LIST", raising=False)
    monkeypatch.delenv("CAPSOLVER_API_KEY", raising=False)
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "tenant.json").write_text(
        json.dumps({"tenant_id": "t_rt2", "display_name": "Runtime", "sources": []}),
        encoding="utf-8",
    )
    _install_fake_fastmcp(monkeypatch)

    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str] | None = None) -> object:
            del url, headers
            raise ConnectionError("refused")

    monkeypatch.setattr("httpx.AsyncClient", _Client)
    from job_ftch.adapters.mcp import server as mcp_server
    from job_ftch.adapters.mcp.server import create_server
    from job_ftch.config import Settings

    monkeypatch.setattr(mcp_server, "_residential_yaml_urls", lambda: [])

    settings = Settings(
        llm_backend="openai",
        openai_api_key="k",  # type: ignore[arg-type]
        openai_base_url="http://127.0.0.1:8317/v1",
        openai_model="missing",
        captcha_provider="capsolver",
        captcha_enabled_providers=["browser_wait", "capsolver"],
        store_backend="sqlite",
        job_backend="sqlite",
        search_backend="sqlite",
        job_group_store_backend="sqlite",
        proxy_gateway="",
    )
    server = create_server(configs_dir=configs_dir, base_settings=settings)
    payload = await server.app.tools["get_runtime"]()
    assert payload["residential_proxies"] == {
        "configured": False,
        "reachable": False,
        "error_class": None,
    }
    assert payload["llm"]["ok"] is False
    solvers = {item["id"]: item for item in payload["captcha_solvers"]}
    assert solvers["capsolver"]["key_present"] is False
    blob = json.dumps(payload)
    assert "http://" not in blob or "127.0.0.1:8317" in blob  # llm endpoint is public
    _assert_no_secret_values(payload)


@pytest.mark.asyncio
async def test_mcp_get_runtime_heuristic_llm_skips_gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JOB_FTCH_RESIDENTIAL_PROXY_LIST", raising=False)
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "tenant.json").write_text(
        json.dumps({"tenant_id": "t_rt3", "display_name": "Runtime", "sources": []}),
        encoding="utf-8",
    )
    _install_fake_fastmcp(monkeypatch)
    from job_ftch.adapters.mcp.server import create_server
    from job_ftch.config import Settings

    settings = Settings(
        llm_backend="heuristic",
        store_backend="sqlite",
        job_backend="sqlite",
        search_backend="sqlite",
        job_group_store_backend="sqlite",
        proxy_gateway="",
    )
    server = create_server(configs_dir=configs_dir, base_settings=settings)
    payload = await server.app.tools["get_runtime"]()
    assert payload["llm"]["ok"] is True
    assert payload["llm"]["error"] == "backend_not_openai_compatible"


@pytest.mark.asyncio
async def test_mcp_doctor_narrates_runtime_without_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "JOB_FTCH_RESIDENTIAL_PROXY_LIST",
        "http://user:s3cret@10.9.8.7:8080",  # pragma: allowlist secret
    )
    monkeypatch.setenv("CAPSOLVER_API_KEY", "cap-secret-value")  # pragma: allowlist secret
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "tenant.json").write_text(
        json.dumps({"tenant_id": "t_doc", "display_name": "Doctor", "sources": []}),
        encoding="utf-8",
    )
    _install_fake_fastmcp(monkeypatch)

    class _Resp:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {"data": [{"id": "gpt-5.4-mini"}]}

    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str, **kwargs: object) -> _Resp:
            del url, kwargs
            return _Resp()

    monkeypatch.setattr("httpx.AsyncClient", _Client)
    from job_ftch.adapters.mcp.server import create_server
    from job_ftch.config import Settings

    settings = Settings(
        llm_backend="openai",
        openai_api_key="sk-live-secret",  # type: ignore[arg-type]
        openai_base_url="http://127.0.0.1:8317/v1",
        openai_model="gpt-5.4-mini",
        captcha_provider="capsolver",
        captcha_enabled_providers=["browser_wait", "capsolver"],
        store_backend="sqlite",
        job_backend="sqlite",
        search_backend="sqlite",
        job_group_store_backend="sqlite",
    )
    server = create_server(configs_dir=configs_dir, base_settings=settings)
    await server.startup()
    payload = await server.app.tools["doctor"]()
    report = payload["report"]
    assert isinstance(report, str) and report.strip()
    assert "patchright" in payload["extras"]
    assert "fastmcp" in payload["extras"]
    assert payload["extras"]["patchright"]["extra"] == "browser"
    assert payload["extras"]["fastmcp"]["extra"] == "mcp"
    assert payload["bypass"].get("capabilities") is not None or payload["bypass"].get("status")
    assert "present" in report or "missing" in report or "degraded" in report
    blob = json.dumps(payload)
    assert "s3cret" not in report
    assert "s3cret" not in blob
    assert "10.9.8.7" not in report
    assert "10.9.8.7" not in blob
    assert "cap-secret-value" not in blob
    assert "sk-live-secret" not in blob
    _assert_no_secret_values(payload)
    await server.shutdown()
