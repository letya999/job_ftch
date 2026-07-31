#!/usr/bin/env python3
"""Sample random jobs from each configured source for golden-test fixtures.

Usage:
    python scripts/publication/sample_sources.py [--count 5] [--output fixtures/publication]

Reads jobs from the configured store (sqlite by default) and writes per-source
JSON fixtures with ~N random examples each. These fixtures feed golden-snapshot
tests that verify the publication renderer produces stable, high-quality output.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Sample jobs for publication fixtures")
    parser.add_argument("--count", type=int, default=5, help="Samples per source")
    parser.add_argument(
        "--output",
        type=str,
        default="fixtures/publication",
        help="Output directory",
    )
    parser.add_argument("--db", type=str, default=None, help="SQLite DB path")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        from job_ftch.infrastructure.stores.sqlite import SQLiteStore  # noqa: F401
    except ImportError:
        print("SQLiteStore not available; generating synthetic fixtures instead.")
        _generate_synthetic(output_dir, args.count)
        return

    if args.db:
        db_path = Path(args.db)
    else:
        default_paths = [
            Path("data/jobs.db"),
            Path("jobs.db"),
            Path.home() / ".job_ftch" / "jobs.db",
        ]
        db_path = next((p for p in default_paths if p.exists()), None)

    if db_path is None or not db_path.exists():
        print(f"No database found. Generating synthetic fixtures in {output_dir}/")
        _generate_synthetic(output_dir, args.count)
        return

    import asyncio

    asyncio.run(_sample_from_db(db_path, output_dir, args.count))


async def _sample_from_db(db_path: Path, output_dir: Path, count: int) -> None:
    from job_ftch.infrastructure.stores.sqlite import SQLiteStore

    store = SQLiteStore(str(db_path))

    sources: dict[str, list[dict]] = {}
    jobs = await store.list_jobs(limit=5000)

    for job in jobs:
        source = getattr(job, "source_name", "unknown")
        if source not in sources:
            sources[source] = []
        try:
            sources[source].append(job.model_dump(mode="json"))
        except Exception:
            continue

    total = 0
    for source_name, items in sources.items():
        sampled = random.sample(items, min(count, len(items)))
        slug = source_name.lower().replace(" ", "_").replace("/", "_")[:40]
        out_file = output_dir / f"{slug}.json"
        out_file.write_text(
            json.dumps(sampled, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        total += len(sampled)
        print(f"  {source_name}: {len(sampled)} samples -> {out_file}")

    print(f"\nTotal: {total} samples from {len(sources)} sources")


def _generate_synthetic(output_dir: Path, count: int) -> None:
    """Generate minimal synthetic fixtures for testing without a DB."""
    from job_ftch.domain.models import (
        CompensationPeriod,
        CompensationRange,
        Job,
        SourceKind,
        WorkMode,
    )

    sources = [
        ("hh_ru", SourceKind.CAREER_SITE, "HH.ru"),
        ("habr_career", SourceKind.CAREER_SITE, "Habr Career"),
        ("telegram_ai_jobs", SourceKind.TELEGRAM_CHANNEL, "AI Jobs"),
        ("yandex", SourceKind.CAREER_SITE, "Yandex"),
        ("sber", SourceKind.CAREER_SITE, "Sber"),
    ]

    for slug, kind, name in sources:
        jobs = []
        for i in range(count):
            job = Job(
                raw_item_id=f"syn-{slug}-{i}",
                source_kind=kind,
                source_name=name,
                title=f"ML Engineer #{i + 1}",
                company=f"Company {chr(65 + i)}",
                description=f"Job posting from {name}. Building ML systems.",
                work_mode=random.choice(list(WorkMode)),
                city=random.choice(["Moscow", "Berlin", "Remote", None]),
                country=random.choice(["Russia", "Germany", None]),
                compensation=CompensationRange(
                    currency=random.choice(["RUB", "USD", "EUR"]),
                    min_amount=random.randint(100, 500) * 1000,
                    max_amount=random.randint(500, 800) * 1000,
                    period=CompensationPeriod.MONTH,
                )
                if random.random() > 0.3
                else None,
            )
            jobs.append(job.model_dump(mode="json"))

        out_file = output_dir / f"{slug}.json"
        out_file.write_text(
            json.dumps(jobs, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  {name}: {len(jobs)} synthetic samples -> {out_file}")

    print(f"\nTotal: {count * len(sources)} synthetic samples from {len(sources)} sources")


if __name__ == "__main__":
    main()
