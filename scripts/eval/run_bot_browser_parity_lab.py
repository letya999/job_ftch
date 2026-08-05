from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_TIERS = ("patchright_browser", "nodriver", "camoufox", "cloak")
REQUIRED_MODULES = ("cryptography", "httpx", "hypercorn", "maxminddb", "starlette")


def _split_csv(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return default
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _path_env(*paths: Path, existing: str | None = None) -> str:
    selected = [str(path) for path in paths]
    if existing:
        selected.append(existing)
    return os.pathsep.join(selected)


def missing_runtime_modules() -> tuple[str, ...]:
    return tuple(module for module in REQUIRED_MODULES if importlib.util.find_spec(module) is None)


def run_tier(
    *,
    repo_root: Path,
    lab_root: Path,
    tier: str,
    out_dir: Path,
    timeout_seconds: float,
    headed: bool,
    expect: str,
    port: int,
) -> int:
    tier_out = out_dir / tier
    tier_out.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": _path_env(repo_root, lab_root, existing=env.get("PYTHONPATH")),
            "PARITYLAB_CLIENT_HOOK": "examples.job_ftch_hook:run_owned_browser",
            "PARITYLAB_ROOT": str(lab_root),
            "PARITYLAB_ARTIFACTS": str(tier_out),
            "PARITYLAB_CERTS": str(out_dir / "_certs"),
            "PARITYLAB_PORT": str(port),
            "PARITYLAB_BACKEND_PORT": str(port + 1),
            "JOB_FTCH_PARITY_TIER": tier,
            "JOB_FTCH_PARITY_HEADED": "1" if headed else "0",
        }
    )
    command = [
        sys.executable,
        "-m",
        "paritylab",
        "run-client",
        "project-browser-hook",
        "--gate",
        "--expect",
        expect,
        "--timeout",
        str(timeout_seconds),
    ]
    if headed:
        command.append("--headed")
    print(f"[paritylab] tier={tier} artifacts={tier_out}")
    completed = subprocess.run(command, cwd=lab_root, env=env, check=False)
    return int(completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the local bot parity lab against job_ftch browser tiers."
    )
    parser.add_argument(
        "--tiers",
        default=",".join(DEFAULT_TIERS),
        help=f"comma-separated job_ftch tiers; default: {','.join(DEFAULT_TIERS)}",
    )
    parser.add_argument("--out", default="artifacts/bot_parity_lab")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--base-port", type=int, default=18443)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument(
        "--expect",
        choices=("default", "pass", "fail"),
        default="pass",
        help="paritylab expectation for each gated project-browser-hook run",
    )
    parser.add_argument(
        "--allow-fail-tiers",
        default="",
        help="comma-separated tiers whose non-zero gate result should not fail this wrapper",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    lab_root = repo_root / "tools" / "bot_parity_lab"
    out_dir = (repo_root / args.out).resolve()
    tiers = _split_csv(args.tiers, DEFAULT_TIERS)
    allowed_failures = set(_split_csv(args.allow_fail_tiers, ()))
    missing = missing_runtime_modules()
    if missing:
        print(
            "Missing parity_lab dependencies: "
            + ", ".join(missing)
            + ". Install with `uv sync --extra parity_lab` or `python -m pip install -e .[parity_lab]`.",
            file=sys.stderr,
        )
        return 3

    exit_code = 0
    for index, tier in enumerate(tiers):
        code = run_tier(
            repo_root=repo_root,
            lab_root=lab_root,
            tier=tier,
            out_dir=out_dir,
            timeout_seconds=args.timeout,
            headed=bool(args.headed),
            expect=str(args.expect),
            port=int(args.base_port) + index * 10,
        )
        if code and tier not in allowed_failures:
            exit_code = code
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
