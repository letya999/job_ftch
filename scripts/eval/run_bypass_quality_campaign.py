"""Run the authorized bypass-quality campaign over explicit test targets.

This runner is a gate around the canonical ingest probe. It never discovers
arbitrary public targets and never records tokens, cookies, provider API keys,
or solver response values.
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

import yaml

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

TERMINAL_BUCKETS = {
    "blocked_or_protected",
    "provider_tunnel_denied",
    "soft_403_with_content",
    "waf_challenge",
}
CHALLENGE_BUCKETS = {"waf_challenge", "blocked_or_protected", "protected"}
BLOCKED_OUTCOMES = {"blocked_or_protected", "protected", "blocked"}
MANUAL_OUTCOMES = {"manual_required", "terminal_manual_required"}


def _load_yaml_urls(path: Path) -> list[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    urls = data.get("urls", [])
    if not isinstance(urls, list):
        msg = f"{path} must contain a YAML list under 'urls'"
        raise ValueError(msg)
    return [str(url).strip() for url in urls if str(url).strip()]


def find_authorized_targets(explicit: Path | None) -> tuple[Path, list[str]]:
    if explicit is not None:
        return explicit, _load_yaml_urls(explicit)
    campaign_path = Path(".runtime/campaign_targets.yaml")
    if campaign_path.exists():
        return campaign_path, _load_yaml_urls(campaign_path)

    candidates = sorted(
        Path(".runtime/runs").glob("protected*/targets.yaml"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        msg = (
            "No authorized targets found. Create .runtime/campaign_targets.yaml "
            "with a YAML 'urls' list, or pass --targets."
        )
        raise FileNotFoundError(msg)
    return candidates[0], _load_yaml_urls(candidates[0])


def _safe_runtime_overlay(*, allow_paid: bool) -> dict[str, Any]:
    if allow_paid:
        return {}
    return {
        "captcha_provider": "browser_wait",
        "captcha_enabled_providers": ["browser_wait", "nopecha"],
        "captcha_provider_routes": {
            "recaptcha": ["browser_wait", "manual_required"],
            "recaptcha_v3": ["browser_wait", "manual_required"],
            "cloudflare_challenge": ["browser_wait", "manual_required"],
            "turnstile": ["observe"],
            "hcaptcha": ["observe"],
            "unknown": ["observe"],
        },
    }


def classify_campaign_result(result: dict[str, Any]) -> dict[str, Any]:
    stats = result.get("stats") if isinstance(result.get("stats"), dict) else {}
    challenge_events = stats.get("challenge_events") or []
    captcha_types = stats.get("detected_captcha_types") or []
    detected_config = stats.get("detected_monitor_config") or {}
    failure_bucket = result.get("failure_bucket")
    terminal_outcome = result.get("terminal_outcome")
    fingerprint = result.get("fingerprint_audit") or {}
    parser = result.get("parser_outcome") or {}
    has_challenge = bool(
        challenge_events
        or captcha_types
        or detected_config.get("challenge")
        or failure_bucket in CHALLENGE_BUCKETS
    )
    has_typed_challenge = bool(
        captcha_types
        or any((event.get("type") or "") not in {"", "unknown"} for event in challenge_events)
        or detected_config.get("captcha_type")
    )

    if result.get("parse_status") == "parsed_ok":
        outcome = "parsed_ok"
    elif terminal_outcome in MANUAL_OUTCOMES:
        outcome = "terminal_manual_required"
    elif has_challenge:
        outcome = "classified_challenge"
    elif failure_bucket in BLOCKED_OUTCOMES or terminal_outcome in BLOCKED_OUTCOMES:
        outcome = "classified_blocked"
    else:
        outcome = "terminal_manual_required"

    issues: list[str] = []
    if has_challenge and not has_typed_challenge:
        issues.append("unknown_challenge_with_waf_or_captcha_evidence")
    if (
        int(stats.get("monitor_failure_without_escalation") or 0) > 0
        and has_challenge
        and not result.get("bypass_route_transitions")
    ):
        issues.append("monitor_failure_without_escalation")
    if fingerprint.get("coherent") is False:
        issues.append("suspicious_fingerprint_incoherence")
    if (
        result.get("parse_status") == "parsed_failed"
        and failure_bucket == "unconfirmed_empty"
        and has_challenge
    ):
        issues.append("false_empty_result_from_protection")
    if result.get("deadline_exceeded") or failure_bucket in {
        "timeout_global",
        "global_run_timeout",
        "deadline_exceeded",
    }:
        issues.append("non_terminal_deadline_or_timeout")
    if outcome == "parsed_ok" and not parser.get("items_extracted"):
        issues.append("parsed_ok_without_items_extracted")

    return {
        "url": result.get("url"),
        "outcome": outcome,
        "issues": issues,
        "route": {
            "tier": result.get("bypass_final_tier"),
            "network": result.get("bypass_final_network"),
            "session": result.get("bypass_final_session"),
            "challenge_state": result.get("bypass_final_challenge_state"),
            "transitions": result.get("bypass_route_transitions") or [],
        },
        "telemetry": {
            "attempts": result.get("bypass_attempts") or [],
            "steps": _observed_route_steps(result),
            "challenge_events": challenge_events,
            "captcha_types": captcha_types,
            "fingerprint_audit": fingerprint,
            "parser_outcome": parser,
        },
    }


def _observed_route_steps(result: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    transitions = result.get("bypass_route_transitions") or []
    if isinstance(transitions, list):
        for transition in transitions:
            if not isinstance(transition, dict):
                continue
            from_engine = transition.get("from_engine")
            to_engine = transition.get("to_engine")
            if from_engine and not steps:
                steps.append(
                    {
                        "tier": from_engine,
                        "network": transition.get("network"),
                        "session": transition.get("session"),
                        "challenge_state": transition.get("challenge"),
                        "source": "transition_from",
                    }
                )
            if to_engine:
                steps.append(
                    {
                        "tier": to_engine,
                        "network": transition.get("network"),
                        "session": transition.get("session"),
                        "challenge_state": transition.get("challenge"),
                        "source": "transition_to",
                    }
                )
    final_tier = result.get("bypass_final_tier")
    if final_tier and (not steps or steps[-1].get("tier") != final_tier):
        steps.append(
            {
                "tier": final_tier,
                "network": result.get("bypass_final_network"),
                "session": result.get("bypass_final_session"),
                "challenge_state": result.get("bypass_final_challenge_state"),
                "source": "final_route",
            }
        )
    return steps


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    classified = [classify_campaign_result(result) for result in results]
    issues = [issue for row in classified for issue in row["issues"]]
    return {
        "total": len(classified),
        "outcomes": dict(Counter(row["outcome"] for row in classified)),
        "issues": dict(Counter(issues)),
        "targets": classified,
        "passed": not issues
        and all(
            row["outcome"]
            in {
                "parsed_ok",
                "classified_challenge",
                "classified_blocked",
                "terminal_manual_required",
            }
            for row in classified
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--targets", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--hard-cancel-grace", type=float, default=10.0)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--max-items", type=int, default=1)
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(".runtime/runs/bypass_quality_campaign"),
    )
    args = parser.parse_args()

    targets_path, urls = find_authorized_targets(args.targets)
    urls = urls[: args.limit]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    run_targets_path = args.out_dir / "targets.yaml"
    run_targets_path.write_text(
        yaml.safe_dump({"urls": urls}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    runtime_overlay = args.out_dir / "runtime_campaign.yaml"
    runtime_overlay.write_text(
        yaml.safe_dump(_safe_runtime_overlay(allow_paid=args.allow_paid), sort_keys=False),
        encoding="utf-8",
    )
    ingest_json = args.out_dir / "ingest.json"
    slow_yaml = args.out_dir / "slow.yaml"

    env = os.environ.copy()
    env.setdefault("JOB_FTCH_LLM_BACKEND", "heuristic")
    env["JOB_FTCH_RUNTIME_CONFIG_PATH"] = ";".join(
        ["config/runtime.yaml", "config/runtime.dev.yaml", str(runtime_overlay)]
    )
    cmd = [
        sys.executable,
        "scripts/run_ingest_batch.py",
        "--input",
        str(run_targets_path),
        "--out-json",
        str(ingest_json),
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
    completed = subprocess.run(cmd, cwd=Path.cwd(), env=env, check=False)
    results = json.loads(ingest_json.read_text(encoding="utf-8")) if ingest_json.exists() else []
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "source_targets_path": str(targets_path),
        "targets_path": str(run_targets_path),
        "allow_paid": bool(args.allow_paid),
        "ingest_returncode": completed.returncode,
        "summary": summarize(results if isinstance(results, list) else []),
    }
    report_path = args.out_dir / "campaign_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {report_path}")
    return 0 if completed.returncode == 0 and report["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
