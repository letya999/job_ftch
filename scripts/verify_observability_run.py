"""Verify one run across OpenObserve logs and metrics."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import httpx

from job_ftch.config import get_settings
from job_ftch.infrastructure.observability.openobserve import _resolve_openobserve_url


def _openobserve(run_id: str) -> dict[str, Any]:
    settings = get_settings()
    if not (
        settings.openobserve_url and settings.openobserve_username and settings.openobserve_password
    ):
        return {"configured": False}
    base = _resolve_openobserve_url(str(settings.openobserve_url))
    auth = (
        settings.openobserve_username,
        settings.openobserve_password.get_secret_value(),
    )
    stream = settings.openobserve_logs_stream.replace('"', '""')
    literal = run_id.replace("'", "''")
    # OpenObserve's search API accepts SQL rather than bound parameters. Both
    # interpolated values are escaped immediately above before composition.
    sql = (  # nosec B608
        f'SELECT source_run_id, source_id, source_kind, count(*) AS rows FROM "{stream}" '  # nosec B608
        f"WHERE source_run_id = '{literal}' OR body LIKE '%{literal}%' "
        "GROUP BY source_run_id, source_id, source_kind"
    )
    end_time = int(time.time() * 1_000_000)
    with httpx.Client(auth=auth, timeout=30.0) as client:
        response = client.post(
            f"{base}/api/{settings.openobserve_org}/_search",
            json={
                "query": {
                    "sql": sql,
                    "start_time": end_time - 7 * 24 * 60 * 60 * 1_000_000,
                    "end_time": end_time,
                    "from": 0,
                    "size": 20,
                }
            },
        )
        streams_response = client.get(
            f"{base}/api/{settings.openobserve_org}/streams",
            params={"type": "metrics", "fetchSchema": "false"},
        )
    payload = response.json() if response.status_code == 200 else {}
    stream_payload = streams_response.json() if streams_response.status_code == 200 else {}
    streams = stream_payload.get("list") or stream_payload.get("data") or []
    metric_streams = sorted(
        str(item.get("name"))
        for item in streams
        if isinstance(item, dict) and "job_ftch" in str(item.get("name", ""))
    )
    return {
        "configured": True,
        "search_status": response.status_code,
        "hits": payload.get("hits", []),
        "search_error": (payload or response.text[:500] if response.status_code != 200 else None),
        "metric_stream_status": streams_response.status_code,
        "job_ftch_metric_streams": metric_streams,
    }


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _openobserve_rows(report: dict[str, Any]) -> int:
    hits = report.get("hits")
    if not isinstance(hits, list):
        return 0
    return sum(_int_value(row.get("rows")) for row in hits if isinstance(row, dict))


def _probe_backend(name: str, run_id: str, probe: Any) -> dict[str, Any]:
    try:
        result = probe(run_id)
    except httpx.HTTPError as exc:
        return {
            "configured": True,
            "error": f"{type(exc).__name__}: {exc}",
        }
    if isinstance(result, dict):
        return result
    return {
        "configured": False,
        "error": f"{name} probe returned malformed result",
    }


def _evaluate(report: dict[str, Any], args: argparse.Namespace) -> list[str]:
    failures: list[str] = []
    openobserve = report.get("openobserve")
    if not isinstance(openobserve, dict):
        failures.append("openobserve report is malformed")
        openobserve = {}

    if args.require_openobserve and not openobserve.get("configured"):
        failures.append("OpenObserve is required but not configured")
    if openobserve.get("error"):
        failures.append(f"OpenObserve probe failed: {openobserve['error']}")
    elif openobserve.get("configured"):
        if _int_value(openobserve.get("search_status")) != 200:
            failures.append("OpenObserve log search did not return HTTP 200")
        if _int_value(openobserve.get("metric_stream_status")) != 200:
            failures.append("OpenObserve metric stream lookup did not return HTTP 200")
    if args.min_openobserve_rows is not None and not openobserve.get("error"):
        rows = _openobserve_rows(openobserve)
        if rows < args.min_openobserve_rows:
            failures.append(
                f"OpenObserve rows {rows} below required minimum {args.min_openobserve_rows}"
            )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--out-json", type=Path)
    parser.add_argument("--require-openobserve", action="store_true")
    parser.add_argument("--min-openobserve-rows", type=int)
    args = parser.parse_args()
    report = {
        "run_id": args.run_id,
        "openobserve": _probe_backend("OpenObserve", args.run_id, _openobserve),
    }
    failures = _evaluate(report, args)
    report["status"] = "fail" if failures else "pass"
    report["failures"] = failures
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out_json is not None:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
