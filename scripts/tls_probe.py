"""TLS fingerprint probe (Wave 0.2).

Probes curl_cffi's TLS fingerprint against tls.peet.ws/api/all and
compares JA3/JA4/JA4H/Akamai-h2 with the committed baseline.

Usage:
    uv run python scripts/tls_probe.py [--save-baseline] [--impersonate PROFILE]

Results are saved via FingerprintBaselineStore under scope="tls".
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_ftch.infrastructure.bypass.fingerprint_baseline import (
    BaselineRecord,
    FingerprintBaselineStore,
    compare_tls,
)

# Default impersonation profiles matching our bypass tiers.
_DEFAULT_PROFILES = ("chrome131", "chrome124", "safari17_0", "firefox125")

_TLS_PROBE_URL = "https://tls.peet.ws/api/all"
_BROWSERLEAKS_URL = "https://browserleaks.com/json"


async def _probe_tls(impersonate: str) -> dict | None:
    """Probe TLS fingerprint using curl_cffi with given impersonation."""
    from job_ftch.infrastructure.bypass.curl_bypass import _CurlSession

    if _CurlSession is None:
        print("curl_cffi not installed — cannot probe TLS", file=sys.stderr)
        return None

    async with _CurlSession(impersonate=impersonate) as session:
        try:
            resp = await session.get(_TLS_PROBE_URL, timeout=15)
            if resp.status_code != 200:
                print(f"  tls.peet.ws returned {resp.status_code}", file=sys.stderr)
                return None
            data = resp.json()
        except Exception as exc:
            print(f"  tls.peet.ws error: {exc}", file=sys.stderr)
            return None

    # Extract the fields we care about.
    result: dict = {
        "impersonate": impersonate,
        "ja3_hash": data.get("ja3_hash", ""),
        "ja3": data.get("ja3", ""),
        "ja4": data.get("ja4", ""),
        "ja4_h": "",
        "akamai_h2_fingerprint": data.get("akamai_h2", ""),
        "tls_version": data.get("tls_version", ""),
        "cipher_suite": data.get("cipher_suite", ""),
        "http_version": data.get("http_version", ""),
    }
    # JA4H may be nested.
    if "ja4_h" in data:
        result["ja4_h"] = data["ja4_h"]
    elif "peetprint" in data:
        pp = data["peetprint"]
        if isinstance(pp, dict):
            result["ja4_h"] = pp.get("ja4h", "")

    return result


async def main() -> int:
    parser = argparse.ArgumentParser(description="TLS fingerprint probe")
    parser.add_argument(
        "--save-baseline", action="store_true", help="Save results as the new baseline"
    )
    parser.add_argument(
        "--impersonate", type=str, default=None, help="Probe a single impersonation profile"
    )
    args = parser.parse_args()

    store = FingerprintBaselineStore()
    profiles = (args.impersonate,) if args.impersonate else _DEFAULT_PROFILES
    now_iso = datetime.now(UTC).isoformat()

    failures: list[str] = []

    for profile in profiles:
        print(f"Probing TLS for {profile}...", end=" ")
        result = await _probe_tls(profile)

        if result is None:
            print("SKIP (probe failed)")
            continue

        if args.save_baseline:
            record = BaselineRecord(
                persona_name=profile,
                scope="tls",
                generated_at=now_iso,
                payload=result,
            )
            await store.save(record)
            print(f"SAVED (JA4={result.get('ja4', '?')})")
        else:
            baseline = await store.load(profile, "tls")
            if baseline:
                diff = compare_tls(baseline, result)
                if diff.matched:
                    print("OK")
                else:
                    print(f"DIFF: {diff.diffs}")
                    failures.append(f"{profile}: {diff.diffs}")
                if diff.warnings:
                    print(f"  warnings: {diff.warnings}")
            else:
                print(f"NO BASELINE (JA4={result.get('ja4', '?')}) — run with --save-baseline")

    if failures:
        print(f"\n{len(failures)} TLS mismatch(es):")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"\nAll {len(profiles)} profile(s) probed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
