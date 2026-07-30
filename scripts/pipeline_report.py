"""Pipeline performance HTML report generator.

Reads results from a local JSON file (produced by run_experiment.py --out)
and optionally fetches aggregate scores from Langfuse Metrics API.

Usage:
    python scripts/pipeline_report.py
    python scripts/pipeline_report.py --results results/exp_20260618.json --open
    python scripts/pipeline_report.py --no-langfuse
"""

import argparse
import base64
import json
import os
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


def _langfuse_env() -> tuple[str | None, str | None, str]:
    public_key = os.environ.get("JOB_FTCH_LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("JOB_FTCH_LANGFUSE_SECRET_KEY")
    host = os.environ.get("JOB_FTCH_LANGFUSE_HOST", "https://cloud.langfuse.com")
    return public_key, secret_key, host


def load_results(path: Path) -> dict[str, Any]:
    """Load JSON experiment results."""
    if not path.exists():
        print(f"Error: {path} not found")
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)  # type: ignore


def fetch_langfuse_scores(public_key: str, secret_key: str, host: str) -> dict[str, Any]:
    """Fetch exit_stage_match score distribution from Langfuse Metrics API v2."""
    url = f"{host.rstrip('/')}/api/public/v2/metrics"
    auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}"}

    # We want metrics for 'exit_stage_match' score
    params = {
        "name": "exit_stage_match",
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            return response.json()  # type: ignore
        else:
            print(f"Langfuse API warning: {response.status_code} {response.text}")
            return {}
    except Exception as e:
        print(f"Langfuse API error: {e}")
        return {}


def generate_funnel_svg(actual_counts: dict[str, int], expected_counts: dict[str, int]) -> str:
    """Generate a simple SVG horizontal bar chart for the funnel."""
    stages = ["ACCEPT", "hard_filter", "semantic_prefilter"]
    max_val = max(max(actual_counts.values() or [1]), max(expected_counts.values() or [1]))
    width = 400
    row_height = 40
    padding = 100

    svg_parts = [
        f'<svg width="{width + padding}" height="{len(stages) * row_height * 2}" xmlns="http://www.w3.org/2000/svg">'
    ]

    for i, stage in enumerate(stages):
        # Expected bar
        exp_val = expected_counts.get(stage, 0)
        exp_w = (exp_val / max_val) * width if max_val > 0 else 0
        y = i * row_height * 2
        svg_parts.append(
            f'<rect x="{padding}" y="{y}" width="{exp_w}" height="15" fill="#94a3b8" rx="2" />'
        )
        svg_parts.append(
            f'<text x="0" y="{y + 12}" font-family="sans-serif" font-size="12" fill="#64748b">{stage} (exp)</text>'
        )
        svg_parts.append(
            f'<text x="{padding + exp_w + 5}" y="{y + 12}" font-family="sans-serif" font-size="12" fill="#64748b">{exp_val}</text>'
        )

        # Actual bar
        act_val = actual_counts.get(stage, 0)
        act_w = (act_val / max_val) * width if max_val > 0 else 0
        y_act = y + 20
        svg_parts.append(
            f'<rect x="{padding}" y="{y_act}" width="{act_w}" height="15" fill="#3b82f6" rx="2" />'
        )
        svg_parts.append(
            f'<text x="0" y="{y_act + 12}" font-family="sans-serif" font-size="12" font-weight="bold" fill="#1e40af">{stage} (act)</text>'
        )
        svg_parts.append(
            f'<text x="{padding + act_w + 5}" y="{y_act + 12}" font-family="sans-serif" font-size="12" font-weight="bold" fill="#1e40af">{act_val}</text>'
        )

    svg_parts.append("</svg>")
    return "\n".join(svg_parts)


def build_html(data: dict[str, Any], lf_metrics: dict[str, Any] = None) -> str:  # type: ignore
    """Build a self-contained HTML report."""
    metrics = data.get("metrics", {})
    results = data.get("results", [])
    run_at = data.get("run_at", datetime.now().isoformat())

    # Aggregates for funnel
    actual_counts = {}  # type: ignore
    expected_counts = {}  # type: ignore
    for r in results:
        act = r["actual_exit"] or "ACCEPT"
        exp = r["expected_exit"] or "ACCEPT"
        actual_counts[act] = actual_counts.get(act, 0) + 1
        expected_counts[exp] = expected_counts.get(exp, 0) + 1

    # Per-source errors
    source_stats = {}
    for r in results:
        src = r["source_name"]
        if src not in source_stats:
            source_stats[src] = {"total": 0, "errors": 0}
        source_stats[src]["total"] += 1
        if not r["match"]:
            source_stats[src]["errors"] += 1

    sorted_sources = sorted(
        source_stats.items(),
        key=lambda x: x[1]["errors"] / x[1]["total"] if x[1]["total"] > 0 else 0,
        reverse=True,
    )

    # Confusion matrix prep
    stages = ["ACCEPT", "hard_filter", "semantic_prefilter"]
    confusion_matrix = metrics.get("confusion", {})

    html_template = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pipeline Performance Report</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; line-height: 1.5; color: #1a202c; max-width: 1000px; margin: 0 auto; padding: 2rem; background: #f7fafc; }
        .card { background: white; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); padding: 1.5rem; margin-bottom: 2rem; }
        h1 { font-size: 1.875rem; font-weight: 700; margin-bottom: 1rem; color: #2d3748; }
        h2 { font-size: 1.25rem; font-weight: 600; margin-top: 0; margin-bottom: 1rem; color: #4a5568; border-bottom: 1px solid #edf2f7; padding-bottom: 0.5rem; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }
        .stat-box { padding: 1rem; background: #edf2f7; border-radius: 6px; text-align: center; }
        .stat-val { font-size: 1.5rem; font-weight: 700; color: #2b6cb0; }
        .stat-label { font-size: 0.875rem; color: #718096; text-transform: uppercase; }
        table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; }
        th { text-align: left; background: #f8fafc; padding: 0.75rem; border-bottom: 2px solid #e2e8f0; font-size: 0.875rem; text-transform: uppercase; color: #64748b; }
        td { padding: 0.75rem; border-bottom: 1px solid #e2e8f0; font-size: 0.9375rem; }
        .match-true { color: #38a169; font-weight: 600; }
        .match-false { color: #e53e3e; font-weight: 600; }
        .matrix-cell { text-align: center; padding: 1rem; }
        .matrix-diag { background: #f0fff4; font-weight: bold; }
        .matrix-err { background: #fff5f5; }
        .funnel-container { display: flex; justify-content: center; padding: 1rem; }
        footer { margin-top: 4rem; font-size: 0.875rem; color: #a0aec0; text-align: center; }
    </style>
</head>
<body>
    <h1>Pipeline Performance Report</h1>
    <p style="color: #718096; margin-bottom: 2rem;">Run at: $run_at</p>

    <div class="card">
        <h2>Run Summary</h2>
        <div class="grid">
            <div class="stat-box">
                <div class="stat-val">$total</div>
                <div class="stat-label">Total Items</div>
            </div>
            <div class="stat-box">
                <div class="stat-val">$accuracy%</div>
                <div class="stat-label">Overall Accuracy</div>
            </div>
            <div class="stat-box">
                <div class="stat-val">$correct</div>
                <div class="stat-label">Correct Matches</div>
            </div>
        </div>
        
        <table>
            <thead>
                <tr>
                    <th>Class</th>
                    <th>Precision</th>
                    <th>Recall</th>
                    <th>F1 Score</th>
                    <th>Support</th>
                </tr>
            </thead>
            <tbody>
                $per_class_rows
            </tbody>
        </table>
    </div>

    <div class="card">
        <h2>Pipeline Funnel</h2>
        <div class="funnel-container">
            $funnel_svg
        </div>
    </div>

    <div class="card">
        <h2>Confusion Matrix</h2>
        <table>
            <thead>
                <tr>
                    <th>Expected \\ Actual</th>
                    $matrix_header
                </tr>
            </thead>
            <tbody>
                $matrix_rows
            </tbody>
        </table>
    </div>

    <div class="card">
        <h2>Errors by Source</h2>
        <table>
            <thead>
                <tr>
                    <th>Source Name</th>
                    <th>Total</th>
                    <th>Errors</th>
                    <th>Error Rate</th>
                </tr>
            </thead>
            <tbody>
                $source_rows
            </tbody>
        </table>
    </div>

    $langfuse_section

    <footer>
        Generated by scripts/pipeline_report.py
    </footer>
</body>
</html>
"""
    from string import Template

    per_class_rows = ""
    for label, m in metrics.get("per_class", {}).items():
        per_class_rows += f"""
        <tr>
            <td>{label}</td>
            <td>{m["precision"]:.3f}</td>
            <td>{m["recall"]:.3f}</td>
            <td>{m["f1"]:.3f}</td>
            <td>{m["support"]}</td>
        </tr>"""

    matrix_header = "".join(f"<th>{s}</th>" for s in stages)
    matrix_rows = ""
    for exp_s in stages:
        matrix_rows += f"<tr><td><strong>{exp_s}</strong></td>"
        for act_s in stages:
            count = confusion_matrix.get(f"{exp_s}→{act_s}", 0)
            cell_class = "matrix-diag" if exp_s == act_s else ("matrix-err" if count > 0 else "")
            matrix_rows += f'<td class="matrix-cell {cell_class}">{count}</td>'
        matrix_rows += "</tr>"

    source_rows = ""
    for src, stats in sorted_sources:
        rate = (stats["errors"] / stats["total"]) if stats["total"] > 0 else 0
        source_rows += f"""
        <tr>
            <td>{src}</td>
            <td>{stats["total"]}</td>
            <td>{stats["errors"]}</td>
            <td>{rate:.1%}</td>
        </tr>"""

    langfuse_section = ""
    if lf_metrics:
        langfuse_section = f"""
    <div class="card">
        <h2>Langfuse Historical Metrics</h2>
        <pre style="background: #f8fafc; padding: 1rem; border-radius: 6px; overflow: auto; font-size: 0.8rem;">
{json.dumps(lf_metrics, indent=2)}
        </pre>
    </div>"""

    t = Template(html_template)
    return t.substitute(
        run_at=run_at,
        total=metrics.get("total", 0),
        accuracy=round(metrics.get("accuracy", 0) * 100, 1),
        correct=metrics.get("correct", 0),
        per_class_rows=per_class_rows,
        funnel_svg=generate_funnel_svg(actual_counts, expected_counts),
        matrix_header=matrix_header,
        matrix_rows=matrix_rows,
        source_rows=source_rows,
        langfuse_section=langfuse_section,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate pipeline performance report.")
    parser.add_argument(
        "--results", default="results/exp_20260618.json", help="Path to experiment results JSON."
    )
    parser.add_argument("--out", default="results/dashboard.html", help="Path to output HTML.")
    parser.add_argument(
        "--open", action="store_true", help="Open report in browser after generation."
    )
    parser.add_argument("--no-langfuse", action="store_true", help="Skip Langfuse API call.")
    args = parser.parse_args()

    results_path = Path(args.results)
    if not results_path.exists():
        # Try to find latest if default not found
        latest = sorted(Path("results").glob("exp_*.json"))
        if latest:
            results_path = latest[-1]
            print(f"Default results not found, using latest: {results_path}")
        else:
            print("Error: No results found in results/ folder.")
            return

    data = load_results(results_path)
    if not data:
        return

    lf_metrics = None
    if not args.no_langfuse:
        public_key, secret_key, host = _langfuse_env()
        if public_key and secret_key:
            print("Fetching metrics from Langfuse API...")
            lf_metrics = fetch_langfuse_scores(public_key, secret_key, host)

    html = build_html(data, lf_metrics)  # type: ignore

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"Report generated: {out_path.absolute()}")

    if args.open:
        webbrowser.open(f"file://{out_path.absolute()}")


if __name__ == "__main__":
    main()
