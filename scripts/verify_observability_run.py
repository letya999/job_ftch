"""Verify one run across OpenObserve logs/metrics and Langfuse traces."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import httpx

from job_ftch.config import get_settings
from job_ftch.infrastructure.observability.openobserve import _resolve_openobserve_url


def _attributes(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    attributes = metadata.get("attributes")
    return attributes if isinstance(attributes, dict) else {}


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


def _langfuse(run_id: str) -> dict[str, Any]:
    settings = get_settings()
    if not (
        settings.langfuse_host and settings.langfuse_public_key and settings.langfuse_secret_key
    ):
        return {"configured": False}
    base = str(settings.langfuse_host).rstrip("/")
    auth = (
        settings.langfuse_public_key,
        settings.langfuse_secret_key.get_secret_value(),
    )
    with httpx.Client(auth=auth, timeout=30.0) as client:

        def traces(name: str) -> tuple[int, list[dict[str, Any]]]:
            rows: list[dict[str, Any]] = []
            page = 1
            status = 0
            while True:
                response = client.get(
                    f"{base}/api/public/traces",
                    params={"limit": 100, "page": page, "name": name},
                )
                status = response.status_code
                if status != 200:
                    return status, rows
                payload = response.json()
                rows.extend(payload.get("data", []))
                if page >= int(payload.get("meta", {}).get("totalPages", 1) or 1):
                    return status, rows
                page += 1

        trace_status, ingest_traces = traces("ingest.run")
        trace_row = next(
            (
                row
                for row in ingest_traces
                if _attributes(row).get("job_ftch.source_run_id") == run_id
            ),
            None,
        )
        if trace_row is None:
            summary_status, summary_traces = traces("eval.run.summary")
            summary_row = next(
                (
                    row
                    for row in summary_traces
                    if _attributes(row).get("job_ftch.eval.run_name") == run_id
                ),
                None,
            )
            item_status, item_traces = traces("eval.item")
            item_rows = [
                row
                for row in item_traces
                if _attributes(row).get("job_ftch.eval.run_name") == run_id
            ]
            return {
                "configured": True,
                "trace_status": summary_status or item_status or trace_status,
                "trace_found": summary_row is not None,
                "trace_kind": "eval.run.summary" if summary_row is not None else None,
                "trace_id": str(summary_row["id"]) if summary_row is not None else None,
                "trace_attributes": _attributes(summary_row) if summary_row is not None else {},
                "eval_item_trace_status": item_status,
                "eval_item_traces": len(item_rows),
            }
        trace_id = str(trace_row["id"])

        def observations(name: str | None = None) -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            page = 1
            while True:
                params: dict[str, Any] = {
                    "traceId": trace_id,
                    "limit": 100,
                    "page": page,
                }
                if name is not None:
                    params["name"] = name
                response = client.get(
                    f"{base}/api/public/observations",
                    params=params,
                )
                response.raise_for_status()
                payload = response.json()
                rows.extend(payload.get("data", []))
                if page >= int(payload.get("meta", {}).get("totalPages", 1) or 1):
                    return rows
                page += 1

        final_rows = observations("pipeline.run.final")
        decisions = observations("graph.node.decision")
        aggregation = observations("graph.node.aggregation")
        enrichment = observations("graph.node.enrichment")
        all_observations = observations()

    graph_node_map: dict[str, dict[str, Any]] = {}
    for row in all_observations:
        name = str(row.get("name") or "")
        if not name.startswith("graph.node.") or name in graph_node_map:
            continue
        attrs = _attributes(row)
        graph_node_map[name.removeprefix("graph.node.")] = {
            "index": attrs.get("job_ftch.node_index"),
            "params": attrs.get("job_ftch.node.params"),
            "effect": attrs.get("job_ftch.effect"),
            "execution": attrs.get("job_ftch.execution"),
        }

    terminal_statuses: dict[str, int] = {}
    terminal_reasons: dict[str, int] = {}
    for row in decisions:
        attrs = _attributes(row)
        status = str(attrs.get("job_ftch.terminal_status") or "unknown")
        terminal_statuses[status] = terminal_statuses.get(status, 0) + 1
        raw_reasons = attrs.get("job_ftch.terminal_reasons")
        try:
            reasons = json.loads(raw_reasons) if isinstance(raw_reasons, str) else raw_reasons or []
        except json.JSONDecodeError:
            reasons = [str(raw_reasons)]
        for reason in reasons:
            key = str(reason)
            terminal_reasons[key] = terminal_reasons.get(key, 0) + 1

    return {
        "configured": True,
        "trace_status": trace_status,
        "trace_found": True,
        "trace_id": trace_id,
        "trace_attributes": _attributes(trace_row),
        "final_summary_attributes": _attributes(final_rows[0]) if final_rows else {},
        "decision_spans": len(decisions),
        "terminal_statuses": dict(sorted(terminal_statuses.items())),
        "terminal_reasons": dict(sorted(terminal_reasons.items())),
        "post_accept_spans": {
            "aggregation": len(aggregation),
            "enrichment": len(enrichment),
        },
        "graph_node_map": dict(sorted(graph_node_map.items())),
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
    langfuse = report.get("langfuse")
    if not isinstance(openobserve, dict):
        failures.append("openobserve report is malformed")
        openobserve = {}
    if not isinstance(langfuse, dict):
        failures.append("langfuse report is malformed")
        langfuse = {}

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

    if args.require_langfuse and not langfuse.get("configured"):
        failures.append("Langfuse is required but not configured")
    if langfuse.get("error"):
        failures.append(f"Langfuse probe failed: {langfuse['error']}")
    elif langfuse.get("configured"):
        if _int_value(langfuse.get("trace_status")) != 200:
            failures.append("Langfuse trace search did not return HTTP 200")
        if not langfuse.get("trace_found"):
            failures.append("Langfuse trace was not found for run_id")
        item_status = langfuse.get("eval_item_trace_status")
        if item_status is not None and _int_value(item_status) != 200:
            failures.append("Langfuse eval.item trace search did not return HTTP 200")
    if args.expect_eval_item_traces is not None and not langfuse.get("error"):
        count = _int_value(langfuse.get("eval_item_traces"))
        if count != args.expect_eval_item_traces:
            failures.append(
                f"Langfuse eval.item traces {count} != expected {args.expect_eval_item_traces}"
            )
    if args.expect_decision_spans is not None and not langfuse.get("error"):
        count = _int_value(langfuse.get("decision_spans"))
        if count != args.expect_decision_spans:
            failures.append(
                f"Langfuse decision spans {count} != expected {args.expect_decision_spans}"
            )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--out-json", type=Path)
    parser.add_argument("--require-openobserve", action="store_true")
    parser.add_argument("--require-langfuse", action="store_true")
    parser.add_argument("--min-openobserve-rows", type=int)
    parser.add_argument("--expect-eval-item-traces", type=int)
    parser.add_argument("--expect-decision-spans", type=int)
    args = parser.parse_args()
    report = {
        "run_id": args.run_id,
        "openobserve": _probe_backend("OpenObserve", args.run_id, _openobserve),
        "langfuse": _probe_backend("Langfuse", args.run_id, _langfuse),
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
