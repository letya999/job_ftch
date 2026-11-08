from __future__ import annotations

import contextlib
import importlib

import pytest


def test_reconciliation_cli_requires_explicit_operation() -> None:
    try:
        module = importlib.import_module("scripts.reconcile_publications")
    except ImportError:
        module = None
    parser_factory = getattr(module, "build_parser", None)
    assert callable(parser_factory)
    parser = parser_factory()
    assert parser is not None
    with pytest.raises(SystemExit):
        parser.parse_args([])
    args = parser.parse_args(["list", "--tenant", "ai_jobs"])
    assert args.action == "list"
    assert args.tenant == "ai_jobs"


def test_reconciliation_cli_requires_verified_details() -> None:
    module = importlib.import_module("scripts.reconcile_publications")
    parser = module.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["verified-unsent", "--tenant", "ai_jobs"])
    with pytest.raises(SystemExit):
        parser.parse_args(["verified-delivered", "--tenant", "ai_jobs", "--record", "record"])
    args = None
    with contextlib.suppress(SystemExit):
        args = parser.parse_args(
            [
                "verified-delivered",
                "--tenant",
                "ai_jobs",
                "--record",
                "record",
                "--chat-id",
                "-100",
                "--message-id",
                "42",
            ]
        )
    assert args is not None
    assert args.message_id == 42


def test_reconciliation_cli_accepts_explicit_verified_unsent() -> None:
    parser = importlib.import_module("scripts.reconcile_publications").build_parser()
    args = None
    with contextlib.suppress(SystemExit):
        args = parser.parse_args(["verified-unsent", "--tenant", "ai_jobs", "--record", "record"])
    assert args is not None
    assert args.record == "record"


@pytest.mark.anyio
async def test_list_action_is_read_only() -> None:
    module = importlib.import_module("scripts.reconcile_publications")
    run = getattr(module, "run_action", None)
    assert callable(run)

    class Store:
        async def list_uncertain_outbox(self, limit: int = 100):
            return ()

    args = module.build_parser().parse_args(["list", "--tenant", "ai_jobs"])
    assert await run(args, Store()) == []


@pytest.mark.anyio
async def test_verified_unsent_action_releases_only_selected_attempt() -> None:
    from job_ftch.application.tenant_store import TenantStore
    from job_ftch.domain import OutboxRecord, OutboxState
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    module = importlib.import_module("scripts.reconcile_publications")
    store = TenantStore("ai_jobs", InMemoryStore())
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "one", "job_id": "job", "target": "@chan"},
        state=OutboxState.UNCERTAIN,
    )
    saved = await store.enqueue_outbox(record)
    args = module.build_parser().parse_args(
        ["verified-unsent", "--tenant", "ai_jobs", "--record", saved.idempotency_key]
    )
    await module.run_action(args, store)
    assert await store.list_uncertain_outbox() == ()


@pytest.mark.anyio
async def test_verified_delivered_action_saves_actual_receipt() -> None:
    from job_ftch.application.tenant_store import TenantStore
    from job_ftch.domain import OutboxRecord, OutboxState
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    module = importlib.import_module("scripts.reconcile_publications")
    store = TenantStore("ai_jobs", InMemoryStore())
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "one", "job_id": "job", "target": "@chan"},
        state=OutboxState.UNCERTAIN,
    )
    saved = await store.enqueue_outbox(record)
    args = module.build_parser().parse_args(
        [
            "verified-delivered",
            "--tenant",
            "ai_jobs",
            "--record",
            saved.idempotency_key,
            "--chat-id",
            "-100",
            "--message-id",
            "42",
        ]
    )
    await module.run_action(args, store)
    assert (await store.enqueue_outbox(record)).state is OutboxState.DELIVERED


@pytest.mark.anyio
async def test_command_uses_requested_tenant_and_closes_runner() -> None:
    from types import SimpleNamespace

    module = importlib.import_module("scripts.reconcile_publications")
    execute = getattr(module, "execute", None)
    assert callable(execute)

    class Store:
        async def list_uncertain_outbox(self, limit: int = 100):
            return ()

    class Runner:
        closed = False

        def get_runtime(self, tenant_id):
            assert tenant_id == "ai_jobs"
            return SimpleNamespace(store=Store())

        async def close(self):
            self.closed = True

    runner = Runner()
    args = module.build_parser().parse_args(["list", "--tenant", "ai_jobs"])
    assert await execute(args, runner) == []
    assert runner.closed


def test_command_entrypoint_outputs_json(monkeypatch, capsys) -> None:
    import json
    from types import SimpleNamespace

    module = importlib.import_module("scripts.reconcile_publications")
    main = getattr(module, "main", None)
    assert callable(main)

    class Store:
        async def list_uncertain_outbox(self, limit: int = 100):
            return ()

    class Runner:
        def get_runtime(self, tenant_id):
            return SimpleNamespace(store=Store())

        async def close(self):
            pass

    monkeypatch.setattr(module, "load_runner", lambda: Runner())
    assert main(["list", "--tenant", "ai_jobs"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_runner_uses_configured_settings(monkeypatch) -> None:
    import job_ftch.cli
    import job_ftch.config

    module = importlib.import_module("scripts.reconcile_publications")
    settings = object()
    runner = object()
    monkeypatch.setattr(job_ftch.config, "get_settings", lambda: settings)
    monkeypatch.setattr(
        job_ftch.cli,
        "_load_tenant_runner",
        lambda received: runner if received is settings else None,
    )
    assert module.load_runner() is runner


def test_script_help_exposes_reconciliation_commands() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "scripts/reconcile_publications.py", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "verified-delivered" in result.stdout


def test_list_command_runs_with_isolated_sqlite(tmp_path) -> None:
    import os
    import subprocess
    import sys
    from pathlib import Path

    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "tenant.yaml").write_text(
        "tenant_id: test_tenant\ndisplay_name: Test\nsources: []\n", encoding="utf-8"
    )
    env = {key: value for key, value in os.environ.items() if not key.startswith("JOB_FTCH_")}
    env.update(
        {
            "JOB_FTCH_CONFIGS_DIR": str(configs),
            "JOB_FTCH_STORE_BACKEND": "sqlite",
            "JOB_FTCH_STORE_PATH": str(tmp_path / "test.db"),
            "PYTHONPATH": str(Path.cwd()),
        }
    )
    result = subprocess.run(
        [sys.executable, "scripts/reconcile_publications.py", "list", "--tenant", "test_tenant"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
