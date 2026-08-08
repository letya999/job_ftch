from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from paritylab.clients import ADAPTERS
from paritylab.clients.base import ClientRunConfig, ClientRunResult
from paritylab.compare import write_comparison
from paritylab.config import LabConfig
from paritylab.models import GateDisposition

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def command_serve(_args: argparse.Namespace) -> int:
    from paritylab.server import run_server

    run_server(LabConfig.from_env())
    return 0


def _configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _start_server_process(config: LabConfig) -> tuple[subprocess.Popen[str], Any]:
    config.artifacts_dir.mkdir(parents=True, exist_ok=True)
    log_path = config.artifacts_dir / "server.log"
    log_handle = log_path.open("a", encoding="utf-8", buffering=1)
    env = os.environ.copy()
    env.update(
        {
            "PARITYLAB_ROOT": str(PROJECT_ROOT),
            "PARITYLAB_HOST": config.host,
            "PARITYLAB_URL_HOST": config.url_host,
            "PARITYLAB_PORT": str(config.public_port),
            "PARITYLAB_BACKEND_PORT": str(config.backend_port),
            "PARITYLAB_ARTIFACTS": str(config.artifacts_dir.relative_to(PROJECT_ROOT))
            if config.artifacts_dir.is_relative_to(PROJECT_ROOT)
            else str(config.artifacts_dir),
            "PARITYLAB_CERTS": str(config.certs_dir.relative_to(PROJECT_ROOT))
            if config.certs_dir.is_relative_to(PROJECT_ROOT)
            else str(config.certs_dir),
            "PARITYLAB_HTTP3": "1" if config.enable_http3 else "0",
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "paritylab", "serve"],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return process, log_handle


def _stop_server_process(process: subprocess.Popen[str], log_handle: Any) -> None:
    if process.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            if os.name == "nt":
                process.terminate()
            else:
                process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
    log_handle.close()


async def _healthcheck(config: LabConfig, timeout_seconds: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    async with httpx.AsyncClient(verify=False, timeout=1.5) as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(f"{config.base_url}/api/health")
                if response.status_code == 200 and response.json().get("ok") is True:
                    return
            except Exception as exc:
                last_error = exc
            await asyncio.sleep(0.15)
    raise RuntimeError(f"lab server did not become healthy: {last_error}")


def _print_results(results: list[ClientRunResult]) -> None:
    headers = ["client", "disposition", "score", "hard", "medium", "low", "session/artifact"]
    rows: list[list[str]] = []
    for result in results:
        if result.skipped:
            rows.append([result.client_name, "skipped", "-", "-", "-", "-", result.skip_reason])
        else:
            artifact = str(result.artifact_dir or result.session_id)
            rows.append(
                [
                    result.client_name,
                    result.disposition.value,
                    str(result.score if result.score is not None else "-"),
                    str(result.hard_count),
                    str(result.medium_count),
                    str(result.low_count),
                    artifact,
                ]
            )
    widths = [len(value) for value in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], min(len(value), 100))
    print("  ".join(value.ljust(widths[index]) for index, value in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        clipped = [value if len(value) <= 100 else value[:97] + "..." for value in row]
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(clipped)))


async def _run_adapters(
    names: list[str],
    *,
    config: LabConfig,
    headless: bool,
    gate_clients: set[str],
    expectation_override: bool | None = None,
    timeout_seconds: float = 45.0,
    baseline_profile: str = "",
) -> list[ClientRunResult]:
    results: list[ClientRunResult] = []
    for name in names:
        adapter = ADAPTERS.get(name)
        if adapter is None:
            results.append(ClientRunResult.skipped_result(name, "unknown", "unknown adapter"))
            continue
        gate = name in gate_clients
        expected_failure = (
            expectation_override
            if expectation_override is not None
            else (False if gate else adapter.default_expected_failure)
        )
        run_config = ClientRunConfig(
            base_url=config.base_url,
            artifacts_dir=config.artifacts_dir,
            headless=headless,
            gate=gate,
            expected_failure=expected_failure,
            timeout_seconds=timeout_seconds,
            baseline_profile=baseline_profile,
        )
        LOGGER.info(
            "running client %s (gate=%s, expected_failure=%s)", name, gate, expected_failure
        )
        try:
            result = await adapter.run(run_config)
        except Exception as exc:
            result = ClientRunResult.skipped_result(
                name,
                adapter.family,
                f"adapter raised {type(exc).__name__}: {exc}",
            )
        results.append(result)
    return results


async def _run_with_server(args: argparse.Namespace, names: list[str]) -> int:
    config = LabConfig.from_env()
    process: subprocess.Popen[str] | None = None
    log_handle: Any = None
    if not args.server_running:
        process, log_handle = _start_server_process(config)
    try:
        await _healthcheck(config)
        results = await _run_adapters(
            names,
            config=config,
            headless=not args.headed,
            gate_clients=set(filter(None, args.gate_clients.split(","))),
            expectation_override=getattr(args, "expectation_override", None),
            timeout_seconds=args.timeout,
            baseline_profile=getattr(args, "baseline_profile", ""),
        )
        _print_results(results)
        failed = [result for result in results if result.disposition == GateDisposition.FAIL]
        if failed:
            print(
                "\nBlocking gate failed: " + ", ".join(result.client_name for result in failed),
                file=sys.stderr,
            )
            return 2
        return 0
    finally:
        if process is not None and log_handle is not None:
            _stop_server_process(process, log_handle)


async def command_run_all(args: argparse.Namespace) -> int:
    names = ["raw-httpx", "curl", "browserish-httpx", "plain-playwright", "project-browser-hook"]
    if args.include_optional:
        names[3:3] = ["patchright", "nodriver", "camoufox"]
    if args.clients:
        names = [name.strip() for name in args.clients.split(",") if name.strip()]
    return await _run_with_server(args, names)


async def command_run_client(args: argparse.Namespace) -> int:
    if args.expect == "fail":
        args.expectation_override = True
    elif args.expect == "pass":
        args.expectation_override = False
    else:
        args.expectation_override = None
    if args.gate:
        args.gate_clients = args.client
        if args.expect == "default":
            args.expectation_override = False
    return await _run_with_server(args, [args.client])


def command_compare(args: argparse.Namespace) -> int:
    output = Path(args.output).resolve()
    json_path, markdown_path = write_comparison(
        Path(args.baseline).resolve(),
        Path(args.candidate).resolve(),
        output,
    )
    print(f"comparison JSON: {json_path}")
    print(f"comparison Markdown: {markdown_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paritylab",
        description="Local defensive bot/browser parity lab",
    )
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="start the local HTTPS playground")
    serve_parser.set_defaults(handler=command_serve)

    run_all = subparsers.add_parser(
        "run-all", help="run negative controls, Playwright, and the project hook"
    )
    run_all.add_argument("--clients", default="", help="comma-separated adapter names")
    run_all.add_argument(
        "--include-optional",
        action="store_true",
        help="also try Patchright, Nodriver, and Camoufox",
    )
    run_all.add_argument(
        "--gate-clients",
        default="patchright,nodriver,camoufox,project-browser-hook",
        help="comma-separated blocking clients",
    )
    run_all.add_argument("--headed", action="store_true")
    run_all.add_argument("--server-running", action="store_true")
    run_all.add_argument("--timeout", type=float, default=45.0)
    run_all.set_defaults(async_handler=command_run_all)

    run_client = subparsers.add_parser("run-client", help="run one adapter")
    run_client.add_argument("client", choices=sorted(ADAPTERS))
    run_client.add_argument(
        "--gate", action="store_true", help="fail process on medium/high findings"
    )
    run_client.add_argument("--expect", choices=["default", "pass", "fail"], default="default")
    run_client.add_argument("--gate-clients", default="")
    run_client.add_argument("--baseline-profile", default="")
    run_client.add_argument("--headed", action="store_true")
    run_client.add_argument("--server-running", action="store_true")
    run_client.add_argument("--timeout", type=float, default=45.0)
    run_client.set_defaults(async_handler=command_run_client)

    compare = subparsers.add_parser(
        "compare", help="diff a manual baseline artifact against a client artifact"
    )
    compare.add_argument("baseline", help="baseline session directory or raw.json")
    compare.add_argument("candidate", help="candidate session directory or raw.json")
    compare.add_argument(
        "--output", default="artifacts/comparison", help="directory for comparison.json/md"
    )
    compare.set_defaults(handler=command_compare)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    _configure_logging(args.verbose)
    if hasattr(args, "async_handler"):
        return asyncio.run(args.async_handler(args))
    handler = args.handler
    result = handler(args)
    return int(result or 0)
