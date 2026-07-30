"""JobSpy vs native parsers comparison spike.

Compares our LinkedIn/Indeed parsers with JobSpy on the same queries.
Results are a measurement report, NOT intended for merge.

Usage:
    pip install python-jobspy
    python scripts/bench_jobspy_vs_native.py

Gating criteria (from plan):
- Accept JobSpy only if >=20% more valid vacancies OR significantly fewer blocks.
- Parity alone is not a reason to adopt an external dependency.
- JobSpy bypasses our bypass layer and SSRF guard -- risk noted.
- LinkedIn/Glassdoor ToS prohibit automated scraping -- legal risk.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

QUERIES = [
    "software engineer remote",
    "data scientist machine learning",
    "devops kubernetes",
    "backend python senior",
]

SITES = ["linkedin", "indeed"]
RESULTS_PER_QUERY = 25


async def _run_native(query: str, site: str) -> dict[str, Any]:
    """Run our native parser for a given site."""
    try:
        if site == "linkedin":
            from job_ftch.infrastructure.sources.site_parsers.linkedin import (  # type: ignore[import]
                parse_listings,
            )
        elif site == "indeed":
            from job_ftch.infrastructure.sources.site_parsers.indeed import (  # type: ignore[import]
                parse_listings,
            )
        else:
            return {"query": query, "site": site, "source": "native", "error": "no parser"}

        start = time.time()
        results = await parse_listings(query, limit=RESULTS_PER_QUERY)
        elapsed = time.time() - start
        jobs = results if isinstance(results, list) else []
        return {
            "query": query,
            "site": site,
            "source": "native",
            "count": len(jobs),
            "with_description": sum(1 for j in jobs if getattr(j, "description", None)),
            "with_company": sum(1 for j in jobs if getattr(j, "company", None)),
            "with_location": sum(
                1 for j in jobs if getattr(j, "location", None) or getattr(j, "locations", None)
            ),
            "elapsed_s": round(elapsed, 2),
        }
    except ImportError:
        return {"query": query, "site": site, "source": "native", "error": "parser not importable"}
    except Exception as exc:
        return {"query": query, "site": site, "source": "native", "error": str(exc)[:200]}


def _run_jobspy(query: str, site: str) -> dict[str, Any]:
    """Run JobSpy for a given site."""
    try:
        from jobspy import scrape_jobs  # type: ignore[import]
    except ImportError:
        return {
            "query": query,
            "site": site,
            "source": "jobspy",
            "error": "pip install python-jobspy",
        }

    try:
        start = time.time()
        df = scrape_jobs(
            site_name=[site],
            search_term=query,
            results_wanted=RESULTS_PER_QUERY,
            country_indeed="USA" if site == "indeed" else None,
        )
        elapsed = time.time() - start
        return {
            "query": query,
            "site": site,
            "source": "jobspy",
            "count": len(df),
            "with_description": int(df["description"].notna().sum())
            if "description" in df.columns
            else 0,
            "with_company": int(df["company_name"].notna().sum())
            if "company_name" in df.columns
            else 0,
            "with_location": int(df["location"].notna().sum()) if "location" in df.columns else 0,
            "elapsed_s": round(elapsed, 2),
        }
    except Exception as exc:
        return {"query": query, "site": site, "source": "jobspy", "error": str(exc)[:200]}


async def main() -> None:
    all_results: list[dict[str, Any]] = []

    for query in QUERIES:
        for site in SITES:
            print(f"\n=== {site} | '{query}' ===")

            native = await _run_native(query, site)
            all_results.append(native)
            print(f"  Native: {native.get('count', '?')} jobs, {native.get('elapsed_s', '?')}s")
            if "error" in native:
                print(f"    Error: {native['error']}")

            jobspy = _run_jobspy(query, site)
            all_results.append(jobspy)
            print(f"  JobSpy: {jobspy.get('count', '?')} jobs, {jobspy.get('elapsed_s', '?')}s")
            if "error" in jobspy:
                print(f"    Error: {jobspy['error']}")

    out_path = Path("data") / f"jobspy_bench_{date.today().isoformat().replace('-', '')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(all_results, indent=2))
    print(f"\nSaved to {out_path}")

    print("\n=== LEGAL NOTE ===")
    print("LinkedIn and Glassdoor ToS prohibit automated scraping.")
    print("Use of JobSpy for these sites requires explicit owner approval.")
    print("See ADR-073 and plan Phase G for gating criteria.")


if __name__ == "__main__":
    asyncio.run(main())
