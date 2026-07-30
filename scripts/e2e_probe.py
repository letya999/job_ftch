"""E2E probe: test each source individually, classify success/error."""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Any

import yaml
from pydantic import TypeAdapter

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from job_ftch.application.registry import create_source_from_spec
from job_ftch.config import get_settings
from job_ftch.domain.source_spec import SourceSpec
from job_ftch.infrastructure.auth.env_auth import EnvAuthProvider

# Configure stdout to handle Unicode by ignoring errors if possible,
# or use a more robust way to print to Windows terminal.
if sys.platform == "win32":
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.ERROR)  # suppress noise

MAX_ITEMS = 3

ERROR_PATTERNS: list[tuple[str, str]] = [
    ("SessionPasswordNeededError", "AUTH_REQUIRED"),
    ("AuthKeyError", "AUTH_REQUIRED"),
    ("UserDeactivatedBanError", "AUTH_REQUIRED"),
    ("FloodWaitError", "RATE_LIMITED"),
    ("ChannelPrivateError", "AUTH_REQUIRED"),
    ("UsernameNotOccupiedError", "NOT_FOUND"),
    ("TimeoutError", "TIMEOUT"),
    ("ClientConnectorError", "NETWORK"),
    ("ClientResponseError", "HTTP_ERROR"),
    ("ConnectionRefusedError", "NETWORK"),
    ("gaierror", "NETWORK"),  # DNS failure
    ("403", "BOT_BLOCKED"),
    ("429", "RATE_LIMITED"),
    ("503", "BOT_BLOCKED"),
    ("CloudFlare", "BOT_BLOCKED"),
    ("Cloudflare", "BOT_BLOCKED"),
    ("ValidationError", "CONFIG_ERROR"),
    ("JobListNotFound", "PARSE_FAILED"),
    ("ParseError", "PARSE_FAILED"),
    ("EOF when reading a line", "AUTH_REQUIRED"),  # Telethon interactive prompt
]


def classify_error(exc: Exception) -> str:
    exc_str = f"{type(exc).__name__}: {exc}"
    for pattern, category in ERROR_PATTERNS:
        if pattern in exc_str:
            return category
    return "UNKNOWN_ERROR"


async def probe_source(source: Any, source_name: str, source_type: str) -> dict[str, Any]:
    start = time.monotonic()
    items: list[Any] = []
    error_category: str | None = None
    error_detail: str = ""

    # Source-type specific timeout
    timeout = 120 if source_type == "career_site" else 30

    try:

        async def collect() -> None:
            async for item in source.fetch():
                items.append(item)
                if len(items) >= MAX_ITEMS:
                    break

        await asyncio.wait_for(collect(), timeout=timeout)
        status = "OK" if items else "EMPTY"
    except TimeoutError:
        error_category = "TIMEOUT"
        error_detail = f">{timeout}s"
        status = "TIMEOUT"
    except Exception as exc:
        error_category = classify_error(exc)
        error_detail = str(exc)[:120]
        status = error_category

    elapsed = time.monotonic() - start
    return {
        "source": source_name,
        "status": status,
        "items": len(items),
        "elapsed": f"{elapsed:.1f}s",
        "error": error_detail,
    }


async def main() -> None:
    config_path = Path(__file__).parent.parent / "config" / "sources_e2e_20260613.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    specs_data = raw.get("sources", [])

    print(f"Probing {len(specs_data)} sources (dynamic timeouts, max_items={MAX_ITEMS})\n")
    print(f"{'SOURCE':<35} {'TYPE':<20} {'STATUS':<18} {'ITEMS':>5} {'ELAPSED':>8}  NOTE")
    print("-" * 110)

    get_settings()
    auth = EnvAuthProvider()
    source_adapter = TypeAdapter(SourceSpec)  # type: ignore

    results: list[dict[str, Any]] = []
    for spec_dict in specs_data:
        source_name = spec_dict.get("source_name", spec_dict.get("entity", "?"))
        source_type = spec_dict.get("type", "?")

        try:
            # Parse dict to SourceSpec using discriminated union
            spec = source_adapter.validate_python(spec_dict)
            source = create_source_from_spec(spec, auth=auth)
            result = await probe_source(source, source_name, source_type)
        except Exception as exc:
            result = {
                "source": source_name,
                "status": "CONFIG_ERROR",
                "items": 0,
                "elapsed": "0.0s",
                "error": str(exc)[:120],
            }

        results.append({**result, "type": source_type})
        note = result["error"] if result["status"] not in ("OK", "EMPTY") else ""
        print(
            f"{source_name:<35} {source_type:<20} {result['status']:<18} "
            f"{result['items']:>5} {result['elapsed']:>8}  {note}"
        )

    # Summary by category
    from collections import Counter

    counts = Counter(r["status"] for r in results)
    print("\n--- Summary -------------------------------")
    for status, count in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {status:<20} {count:>3}")
    print(f"  {'TOTAL':<20} {len(results):>3}")


if __name__ == "__main__":
    asyncio.run(main())
