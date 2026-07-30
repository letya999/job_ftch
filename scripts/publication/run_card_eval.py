#!/usr/bin/env python3
"""Evaluate publication card quality across source fixtures.

Usage:
    python scripts/publication/run_card_eval.py [--fixtures fixtures/publication]

Loads JSON fixtures, builds PublicationCards, renders them, and reports
quality metrics per source and overall.

Metrics:
- field_coverage: fraction of non-empty optional fields
- banlist_clean: fraction of cards without banlist phrases
- length_ok: fraction of cards within Telegram limits
- no_title_echo: fraction without title duplicated in summary
- salary_has_currency: fraction with currency symbol in salary
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from job_ftch.domain.models import Job
from job_ftch.publication.card import build_card
from job_ftch.publication.layout import load_layout
from job_ftch.publication.render import render_card
from job_ftch.publication.validate import validate_card


def evaluate_fixture(fixture_path: Path, layout) -> dict:
    """Evaluate a single fixture file. Returns metrics dict."""
    with fixture_path.open(encoding="utf-8") as f:
        raw_jobs = json.load(f)

    metrics = {
        "source": fixture_path.stem,
        "total": len(raw_jobs),
        "build_ok": 0,
        "validate_ok": 0,
        "render_ok": 0,
        "field_coverage_sum": 0.0,
        "banlist_warnings": 0,
        "title_echo_warnings": 0,
        "salary_no_digits": 0,
        "length_exceeded": 0,
    }

    optional_fields = ("company", "location", "salary", "summary", "key_requirements", "stack")

    for raw in raw_jobs:
        try:
            job = Job.model_validate(raw)
        except Exception:
            continue

        try:
            card = build_card(job)
            metrics["build_ok"] += 1
        except Exception:
            continue

        outcome = validate_card(card, layout)
        if outcome.ok:
            metrics["validate_ok"] += 1

        filled = sum(1 for f in optional_fields if getattr(card, f, None))
        metrics["field_coverage_sum"] += filled / len(optional_fields)

        for w in outcome.warnings:
            if "banlist" in w:
                metrics["banlist_warnings"] += 1
            if "title_echo" in w:
                metrics["title_echo_warnings"] += 1
            if "salary_no_digits" in w:
                metrics["salary_no_digits"] += 1

        try:
            text = render_card(card, layout, profile="channel")
            metrics["render_ok"] += 1
            if len(text) > 4096:
                metrics["length_exceeded"] += 1
        except Exception:
            continue

    return metrics


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate publication card quality")
    parser.add_argument(
        "--fixtures",
        type=str,
        default="fixtures/publication",
        help="Fixtures directory",
    )
    parser.add_argument("--layout", type=str, default=None, help="Card layout YAML path")
    args = parser.parse_args()

    fixtures_dir = Path(args.fixtures)
    if not fixtures_dir.exists():
        print(f"Fixtures directory {fixtures_dir} not found.")
        print("Run scripts/publication/sample_sources.py first to generate fixtures.")
        sys.exit(1)

    layout = load_layout(args.layout)
    fixture_files = sorted(fixtures_dir.glob("*.json"))
    if not fixture_files:
        print(f"No .json fixtures found in {fixtures_dir}")
        sys.exit(1)

    print(f"Evaluating {len(fixture_files)} source fixtures...\n")
    print(
        f"{'Source':<25} {'Total':>5} {'Build':>5} {'Valid':>5} {'Render':>6} {'Coverage':>8} {'Banlist':>7} {'Echo':>5}"
    )
    print("-" * 80)

    all_metrics = []
    for fixture in fixture_files:
        m = evaluate_fixture(fixture, layout)
        all_metrics.append(m)
        total = m["total"]
        if total == 0:
            continue
        coverage = m["field_coverage_sum"] / total if total > 0 else 0
        print(
            f"{m['source']:<25} {total:>5} {m['build_ok']:>5} {m['validate_ok']:>5} "
            f"{m['render_ok']:>6} {coverage:>7.1%} {m['banlist_warnings']:>7} {m['title_echo_warnings']:>5}"
        )

    total_all = sum(m["total"] for m in all_metrics)
    build_all = sum(m["build_ok"] for m in all_metrics)
    valid_all = sum(m["validate_ok"] for m in all_metrics)
    render_all = sum(m["render_ok"] for m in all_metrics)
    coverage_all = (
        sum(m["field_coverage_sum"] for m in all_metrics) / total_all if total_all > 0 else 0
    )

    print("-" * 80)
    print(
        f"{'TOTAL':<25} {total_all:>5} {build_all:>5} {valid_all:>5} "
        f"{render_all:>6} {coverage_all:>7.1%}"
    )
    print(
        f"\nOverall build rate:    {build_all}/{total_all} ({build_all / total_all:.1%})"
        if total_all > 0
        else ""
    )
    print(
        f"Overall validate rate: {valid_all}/{total_all} ({valid_all / total_all:.1%})"
        if total_all > 0
        else ""
    )
    print(
        f"Overall render rate:   {render_all}/{total_all} ({render_all / total_all:.1%})"
        if total_all > 0
        else ""
    )
    print(f"Average field coverage: {coverage_all:.1%}")


if __name__ == "__main__":
    main()
