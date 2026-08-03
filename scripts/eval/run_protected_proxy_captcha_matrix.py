"""Evaluate protected career sites across residential proxy + CAPTCHA routes.

The runner is intentionally metadata-only: it never prints or persists API keys
or proxy credentials. It builds a focused URL queue from previous protected
ingest runs, then invokes the canonical ``scripts/run_ingest_batch.py`` with a
temporary runtime overlay per CAPTCHA provider route.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from job_ftch.infrastructure.bypass.captcha_solver import CAPTCHA_PROVIDER_ENV_KEYS

DEFAULT_HISTORY_PATHS = (
    ".runtime/runs/captcha_observe_300.json",
    ".runtime/runs/captcha_observe_21_recheck.json",
    ".runtime/runs/protected_after_generic_challenge_20260802.json",
    ".runtime/runs/protected_after_force_monitor_20260802.json",
    ".runtime/runs/protected_after_route_defaults_20260802.json",
    ".runtime/runs/protected_after_fixes_allowlist_20260802.json",
    ".runtime/runs/protected_no_linkedin_after_fixes_20260802.json",
    ".runtime/runs/protected_no_linkedin_proxy_20260802.json",
    ".runtime/runs/protected_playwright_diagnostics_20260802.json",
)


def _domain(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.")


def _domain_variants(domain: str) -> set[str]:
    normalized = domain.strip().lower().removeprefix("www.")
    if not normalized:
        return set()
    return {normalized, f"www.{normalized}"}


def _is_protected_record(record: dict[str, Any]) -> bool:
    stats = record.get("stats") or {}
    detected = stats.get("detected_captcha_types") or []
    haystack = " ".join(
        str(record.get(key, ""))
        for key in (
            "failure_bucket",
            "exception",
            "terminal_outcome",
            "bypass_final_network",
            "bypass_final_tier",
        )
    ).casefold()
    return bool(detected) or any(
        marker in haystack
        for marker in (
            "captcha",
            "waf",
            "protected",
            "blocked",
            "challenge",
            "residential_proxy",
        )
    )


def _priority(record: dict[str, Any]) -> tuple[int, int, float]:
    stats = record.get("stats") or {}
    detected = stats.get("detected_captcha_types") or []
    explicit_captcha = bool(detected)
    residential_seen = record.get("bypass_final_network") == "residential_proxy"
    failed = record.get("parse_status") != "parsed_ok"
    elapsed = float(record.get("elapsed_seconds") or 0)
    return (0 if explicit_captcha else 1, 0 if residential_seen else 1, 0 if failed else 1, -elapsed)


def load_protected_targets(paths: list[Path], *, limit: int) -> list[str]:
    rows_by_url: dict[str, dict[str, Any]] = {}
    for path in paths:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            continue
        for record in data:
            if not isinstance(record, dict) or not record.get("url"):
                continue
            if _is_protected_record(record):
                rows_by_url[str(record["url"])] = record

    ordered = sorted(rows_by_url.values(), key=_priority)
    urls: list[str] = []
    domains: set[str] = set()
    for record in ordered:
        url = str(record["url"])
        domain = _domain(url)
        # Prefer domain diversity: one representative first, then fill repeats.
        if domain in domains:
            continue
        urls.append(url)
        domains.add(domain)
        if len(urls) >= limit:
            return urls
    for record in ordered:
        url = str(record["url"])
        if url not in urls:
            urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def load_targets_file(path: Path) -> list[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    urls = data.get("urls", [])
    if not isinstance(urls, list):
        msg = f"{path} must contain a YAML list under 'urls'"
        raise ValueError(msg)
    return [str(url) for url in urls if str(url).strip()]


def _runtime_overlay(provider: str, domains: list[str]) -> dict[str, Any]:
    return {
        "captcha_provider": provider,
        "captcha_enabled_providers": [
            "browser_wait",
            "capsolver",
            "capmonster",
            "nextcaptcha",
            "manual_required",
        ],
        "captcha_provider_routes": {
            "recaptcha": [provider, "manual_required"],
            "recaptcha_v3": [provider, "manual_required"],
            "hcaptcha": [provider, "manual_required"]
            if provider in {"capsolver"}
            else ["observe"],
            "cloudflare_challenge": ["browser_wait", "manual_required"],
            "turnstile": ["capmonster", "manual_required"]
            if provider == "capmonster"
            else ["observe"],
            "unknown": ["observe"],
        },
        "proxy_rescue_allow_domains": domains,
        "bypass_max_route_attempts_per_operation": 8,
        "bypass_max_same_route_retries_per_operation": 8,
        "bypass_max_proxy_rotations_per_operation": 3,
        "bypass_max_source_proxy_rotations": 10,
    }


def _provider_env_status(provider: str) -> dict[str, Any]:
    env_var = CAPTCHA_PROVIDER_ENV_KEYS.get(provider, "")
    value = os.environ.get(env_var, "")
    return {"provider": provider, "env_var": env_var, "has_api_key": bool(value)}


def _post_json(url: str, body: dict[str, Any], *, timeout: int = 30) -> dict[str, Any]:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except json.JSONDecodeError:
            return {"error": str(exc)}
    except OSError as exc:
        return {"error": str(exc)}


def _balances(providers: list[str]) -> dict[str, Any]:
    endpoints = {
        "capmonster": "https://api.capmonster.cloud/getBalance",
        "capsolver": "https://api.capsolver.com/getBalance",
        "nextcaptcha": "https://api.nextcaptcha.com/getBalance",
    }
    balances: dict[str, Any] = {}
    for provider in providers:
        env_var = CAPTCHA_PROVIDER_ENV_KEYS.get(provider, "")
        key = os.environ.get(env_var, "")
        if provider not in endpoints or not key:
            balances[provider] = {"ok": False, "balance": None, "error": "missing_key_or_endpoint"}
            continue
        payload = _post_json(endpoints[provider], {"clientKey": key})
        error = payload.get("errorCode") or payload.get("error")
        balances[provider] = {
            "ok": not bool(error),
            "balance": payload.get("balance", payload.get("balanceAmount")),
            "error": error or "",
        }
    return balances


def _residential_available() -> bool:
    from job_ftch.infrastructure.bypass.proxy_bypass import ResidentialProxyBypass

    try:
        return ResidentialProxyBypass().available
    except Exception:
        return False


def _summarize_results(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    if not isinstance(data, list):
        data = []
    return {
        "total": len(data),
        "parsed_ok": sum(1 for row in data if row.get("parse_status") == "parsed_ok"),
        "items": sum(int(row.get("item_count") or 0) for row in data),
        "failure_buckets": dict(Counter(row.get("failure_bucket") for row in data)),
        "captcha_types": dict(
            Counter(
                ctype
                for row in data
                for ctype in ((row.get("stats") or {}).get("detected_captcha_types") or [])
            )
        ),
        "networks": dict(Counter(row.get("bypass_final_network") for row in data)),
        "tiers": dict(Counter(row.get("bypass_final_tier") for row in data)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", action="append", default=[])
    parser.add_argument("--targets", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--providers", default="capmonster,capsolver,nextcaptcha")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--hard-cancel-grace", type=float, default=10.0)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--global-timeout", type=float, default=None)
    parser.add_argument("--max-items", type=int, default=1)
    parser.add_argument("--out-dir", type=Path, default=Path(".runtime/runs/protected_proxy_captcha_matrix"))
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--require-residential", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    providers = [item.strip() for item in args.providers.split(",") if item.strip()]
    os.environ.setdefault("JOB_FTCH_LLM_BACKEND", "heuristic")
    history_paths = [Path(p) for p in (args.history or DEFAULT_HISTORY_PATHS)]
    args.out_dir.mkdir(parents=True, exist_ok=True)

    urls = load_targets_file(args.targets) if args.targets else load_protected_targets(history_paths, limit=args.limit)
    domains = sorted(
        {
            variant
            for url in urls
            for variant in _domain_variants(_domain(url))
        }
    )
    targets_path = args.out_dir / "targets.yaml"
    targets_path.write_text(yaml.safe_dump({"urls": urls}, allow_unicode=True, sort_keys=False), encoding="utf-8")

    residential_available = _residential_available()
    manifest: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "targets_path": str(targets_path),
        "target_count": len(urls),
        "domains": domains,
        "providers": providers,
        "provider_env": [_provider_env_status(provider) for provider in providers],
        "residential_proxy_available": residential_available,
        "allow_paid": bool(args.allow_paid),
        "runs": [],
    }

    if not args.allow_paid:
        manifest["status"] = "dry_run"
        (args.out_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"wrote {args.out_dir / 'manifest.json'}")
        return 0

    if args.require_residential and not residential_available:
        manifest["status"] = "blocked"
        manifest["blocker"] = "residential_proxy_unavailable"
        (args.out_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"wrote {args.out_dir / 'manifest.json'}")
        return 2

    for provider in providers:
        overlay_path = args.out_dir / f"runtime_{provider}.yaml"
        overlay_path.write_text(
            yaml.safe_dump(_runtime_overlay(provider, domains), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        out_json = args.out_dir / f"ingest_{provider}.json"
        slow_yaml = args.out_dir / f"slow_{provider}.yaml"
        env = os.environ.copy()
        env.setdefault("JOB_FTCH_LLM_BACKEND", "heuristic")
        env["JOB_FTCH_RUNTIME_CONFIG_PATH"] = ";".join(
            ["config/runtime.yaml", "config/runtime.dev.yaml", str(overlay_path)]
        )
        before = _balances(providers)
        cmd = [
            sys.executable,
            "scripts/run_ingest_batch.py",
            "--input",
            str(targets_path),
            "--out-json",
            str(out_json),
            "--slow-queue-out",
            str(slow_yaml),
            "--timeout",
            str(args.timeout),
            "--soft-timeout",
            str(max(1.0, args.timeout - args.hard_cancel_grace - 1.0)),
            "--hard-cancel-grace",
            str(args.hard_cancel_grace),
            "--max-items",
            str(args.max_items),
            "--concurrency",
            str(args.concurrency),
        ]
        if args.global_timeout is not None:
            cmd.extend(["--global-timeout", str(args.global_timeout)])
        completed = subprocess.run(cmd, cwd=Path.cwd(), env=env, check=False)
        after = _balances(providers)
        manifest["runs"].append(
            {
                "provider": provider,
                "returncode": completed.returncode,
                "runtime_overlay": str(overlay_path),
                "out_json": str(out_json),
                "slow_queue": str(slow_yaml),
                "balances_before": before,
                "balances_after": after,
                "summary": _summarize_results(out_json),
            }
        )
        (args.out_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    manifest["status"] = "completed"
    (args.out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {args.out_dir / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
