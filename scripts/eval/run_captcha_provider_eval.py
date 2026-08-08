"""Benchmark configured CAPTCHA providers against one explicit test challenge.

The runner deliberately requires --allow-paid before it calls provider APIs.
It records metadata only: no provider token, cookie, or API key is printed or
written to disk.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

from job_ftch.infrastructure.bypass.captcha_providers import (
    get_captcha_provider_capability,
    list_captcha_providers,
    normalize_challenge_type,
    resolve_captcha_provider,
)
from job_ftch.infrastructure.bypass.captcha_solver import CAPTCHA_PROVIDER_ENV_KEYS


class _StaticSitekeyPage:
    def __init__(self, url: str, sitekey: str) -> None:
        self.url = url
        self._sitekey = sitekey

    async def evaluate(self, script: str) -> str:
        del script
        return self._sitekey


def _default_output_path() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path(".runtime") / "runs" / f"captcha_provider_eval_{stamp}.json"


def _provider_record(provider: str) -> dict[str, Any]:
    capability = get_captcha_provider_capability(provider)
    return {
        "provider": provider,
        "registered": provider in list_captcha_providers(),
        "env_var": CAPTCHA_PROVIDER_ENV_KEYS.get(provider, ""),
        "has_api_key": bool(os.environ.get(CAPTCHA_PROVIDER_ENV_KEYS.get(provider, ""), "")),
        "production_candidate": bool(capability and capability.production_candidate),
        "benchmark_candidate": bool(capability and capability.benchmark_candidate),
        "free_or_dev": bool(capability and capability.free_or_dev),
        "supported_challenge_types": sorted(capability.supported_challenge_types)
        if capability
        else [],
    }


async def _run_provider(
    *,
    provider: str,
    challenge_type: str,
    url: str,
    sitekey: str,
    proxy_url: str,
) -> dict[str, Any]:
    record = _provider_record(provider)
    if not record["registered"]:
        record.update({"status": "skipped", "error": "provider is not registered"})
        return record
    if challenge_type not in record["supported_challenge_types"]:
        record.update({"status": "skipped", "error": "unsupported challenge type"})
        return record
    api_key = os.environ.get(record["env_var"], "")
    if not api_key:
        record.update({"status": "skipped", "error": "missing API key"})
        return record

    started = monotonic()
    result = await resolve_captcha_provider(provider, api_key, proxy_url=proxy_url).solve(
        _StaticSitekeyPage(url, sitekey),
        challenge_type=challenge_type,
        url=url,
        proxy_url=proxy_url,
    )
    elapsed_ms = int((monotonic() - started) * 1000)
    record.update(
        {
            "status": "solved" if result.solved else "failed",
            "challenge_type": result.challenge_type,
            "result_kind": str(result.result_kind.value) if result.result_kind else "",
            "failure_reason": str(result.failure_reason.value) if result.failure_reason else "",
            "error": result.error or "",
            "elapsed_ms": elapsed_ms,
            "provider_task_id_present": bool(result.provider_task_id),
            "token_present": bool(result.tokens.get("captcha_token")),
            "cookie_count": len(result.cookies),
        }
    )
    return record


async def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--sitekey", required=True)
    parser.add_argument("--challenge-type", default="recaptcha")
    parser.add_argument(
        "--providers",
        default="capsolver,capmonster,nextcaptcha,nopecha",
        help="Comma-separated provider names.",
    )
    parser.add_argument("--proxy-url", default="")
    parser.add_argument("--out-json", type=Path, default=_default_output_path())
    parser.add_argument("--allow-paid", action="store_true")
    args = parser.parse_args()

    challenge_type = normalize_challenge_type(args.challenge_type)
    providers = [item.strip() for item in args.providers.split(",") if item.strip()]
    payload: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "url": args.url,
        "challenge_type": challenge_type,
        "providers": providers,
        "allow_paid": args.allow_paid,
        "results": [],
    }

    if not args.allow_paid:
        payload["results"] = [_provider_record(provider) for provider in providers]
        payload["status"] = "dry_run"
    else:
        for provider in providers:
            payload["results"].append(
                await _run_provider(
                    provider=provider,
                    challenge_type=challenge_type,
                    url=args.url,
                    sitekey=args.sitekey,
                    proxy_url=args.proxy_url,
                )
            )
        payload["status"] = "completed"

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {args.out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
