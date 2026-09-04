"""Upsert shipped OpenObserve dashboards using current Settings."""

from __future__ import annotations

from job_ftch.config import get_settings
from job_ftch.infrastructure.observability.openobserve import upsert_openobserve_dashboards


def main() -> None:
    upsert_openobserve_dashboards(get_settings())


if __name__ == "__main__":
    main()
