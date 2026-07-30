"""Build a tenant YAML from the real-world benchmark source catalog."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a tenant config from fixtures/real_world benchmark sources.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("fixtures/real_world/regional_job_sites_freshness.yaml"),
        help="Benchmark source catalog with nested spec entries.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Where to write the tenant YAML.",
    )
    parser.add_argument("--tenant-id", default="perf_20_sites")
    parser.add_argument("--display-name", default="Perf 20 Sites")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument(
        "--country",
        action="append",
        default=None,
        help="Optional country filter. Can be repeated.",
    )
    return parser.parse_args()


def _load_catalog(path: Path) -> list[dict]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sources = payload.get("sources")
    if not isinstance(sources, list):
        raise ValueError(f"Expected a top-level sources list in {path}")
    return [
        item for item in sources if isinstance(item, dict) and isinstance(item.get("spec"), dict)
    ]


def _select_sources(records: list[dict], *, count: int, countries: set[str] | None) -> list[dict]:
    selected: list[dict] = []
    for record in records:
        country = str(record.get("country") or "").upper()
        if countries and country not in countries:
            continue
        selected.append(record["spec"])
        if len(selected) >= count:
            break
    return selected


def main() -> int:
    args = parse_args()
    records = _load_catalog(args.input)
    countries = {item.upper() for item in args.country} if args.country else None
    selected = _select_sources(records, count=args.count, countries=countries)
    if not selected:
        raise SystemExit("No sources selected.")

    tenant = {
        "tenant_id": args.tenant_id,
        "display_name": args.display_name,
        "sources": selected,
        "posting_backend": "none",
        "dry_run": True,
        "output": {"path": f"artifacts/{args.tenant_id}/jobs.json"},
        "review_output": {"path": f"artifacts/{args.tenant_id}/review.jsonl", "jsonl": True},
        "rejected_output": {
            "path": f"artifacts/{args.tenant_id}/rejected.jsonl",
            "jsonl": True,
            "schema_version": "job_ftch.rejected.v1",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.safe_dump(tenant, allow_unicode=False, sort_keys=False),
        encoding="utf-8",
    )
    print(f"Wrote {len(selected)} sources to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
