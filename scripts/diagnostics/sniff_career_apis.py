"""Capture sanitized API/XHR candidates from career pages.

This diagnostic is intentionally separate from ``Source.fetch()``: it helps
turn protected/parser-gap sites into explicit site parsers by showing the
browser-visible JSON endpoints a page uses.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from job_ftch.infrastructure.sources.browser_utils import open_page, run_actions

_JOB_TOKEN_RE = re.compile(
    r"(job|jobs|vacanc|career|careers|position|positions|opening|openings|cariere|rabota)",
    re.IGNORECASE,
)
_TRACKER_RE = re.compile(
    r"(google-analytics|googletagmanager|facebook|doubleclick|sentry|intercom|rudder|"
    r"linkedin|twitter|yandex|metrika|mopinion|visualwebsiteoptimizer)",
    re.IGNORECASE,
)
_SECRET_HEADER_RE = re.compile(r"(authorization|cookie|token|secret|key|session)", re.IGNORECASE)


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _safe_headers(headers: dict[str, str]) -> dict[str, str]:
    allow = {"accept", "content-type", "origin", "referer", "x-requested-with"}
    return {
        key.lower(): value
        for key, value in headers.items()
        if key.lower() in allow and not _SECRET_HEADER_RE.search(key)
    }


def _load_urls(path_or_url: str) -> list[str]:
    if path_or_url.startswith(("http://", "https://")):
        return [path_or_url]
    path = Path(path_or_url)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("urls"), list):
        return [str(url) for url in data["urls"]]
    if isinstance(data, list):
        return [str(url) for url in data]
    raise ValueError("input must be a URL, a YAML list, or a YAML mapping with urls")


def _iter_nodes(node: Any) -> Any:
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _iter_nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_nodes(value)


def _sample_shape(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        keys = sorted(str(key) for key in data)[:30]
        list_counts = {
            str(key): len(value) for key, value in data.items() if isinstance(value, list)
        }
        return {"type": "object", "keys": keys, "list_counts": list_counts}
    if isinstance(data, list):
        first = data[0] if data else None
        keys = sorted(str(key) for key in first)[:30] if isinstance(first, dict) else []
        return {"type": "array", "length": len(data), "item_keys": keys}
    return {"type": type(data).__name__}


def _job_score(url: str, data: Any) -> int:
    score = 0
    if _JOB_TOKEN_RE.search(url):
        score += 5
    for node in _iter_nodes(data):
        if isinstance(node, dict):
            keys = {str(key).casefold() for key in node}
            if keys & {"title", "name", "jobtitle", "job_title", "position"}:
                score += 2
            if keys & {"description", "body", "content", "applyurl", "jobid", "vacancyid"}:
                score += 2
        elif isinstance(node, str) and _JOB_TOKEN_RE.search(node):
            score += 1
        if score >= 20:
            return score
    return score


async def _sniff_one(url: str, args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    captures: list[dict[str, Any]] = []
    errors: list[str] = []
    browser_config = {
        "headless": not args.headful,
        "wait": args.wait,
        "timeout": int(args.timeout * 1000),
        "stealth": True,
        "persistent_context": args.persistent,
    }

    async def _on_response(response: Any) -> None:
        if len(captures) >= args.max_responses:
            return
        response_url = str(response.url)
        if _TRACKER_RE.search(response_url):
            return
        try:
            headers = await response.all_headers()
            content_type = str(headers.get("content-type", ""))
            if "json" not in content_type.casefold() and not _JOB_TOKEN_RE.search(response_url):
                return
            body = await response.body()
            if len(body) > args.max_response_bytes:
                return
            data = json.loads(body)
            request = response.request
            request_headers = getattr(request, "headers", {}) or {}
            captures.append(
                {
                    "url": response_url,
                    "url_hash": _hash_url(response_url),
                    "host": urlparse(response_url).netloc,
                    "method": str(getattr(request, "method", "GET") or "GET").upper(),
                    "status": int(getattr(response, "status", 0) or 0),
                    "content_type": content_type,
                    "request_headers": _safe_headers(dict(request_headers)),
                    "shape": _sample_shape(data),
                    "job_score": _job_score(response_url, data),
                }
            )
        except Exception as exc:
            if len(errors) < 10:
                errors.append(f"{type(exc).__name__}: {exc}")

    try:
        async with open_page(browser_config, use_proxy=args.proxy) as page:
            page.on("response", _on_response)
            await page.goto(url, wait_until=args.wait, timeout=int(args.timeout * 1000))
            if args.actions:
                await run_actions(page, yaml.safe_load(args.actions))
            try:
                await page.wait_for_load_state("networkidle", timeout=int(args.settle * 1000))
            except Exception:
                await page.wait_for_timeout(int(args.settle * 1000))
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")

    ranked = sorted(captures, key=lambda row: (row["job_score"], "api" in row["url"]), reverse=True)
    return {
        "url": url,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "candidate_count": len(ranked),
        "candidates": ranked[: args.max_candidates],
        "errors": errors,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="URL or YAML file with urls")
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--settle", type=float, default=5.0)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--max-responses", type=int, default=80)
    parser.add_argument("--max-response-bytes", type=int, default=1_000_000)
    parser.add_argument("--max-candidates", type=int, default=20)
    parser.add_argument("--wait", default="domcontentloaded")
    parser.add_argument("--actions", default=None, help="YAML list of browser actions")
    parser.add_argument("--proxy", action="store_true")
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--persistent", action="store_true")
    args = parser.parse_args()

    os.environ.setdefault("JOB_FTCH_OPENAI_API_KEY", "sk-local-disabled-for-diagnostics")
    urls = _load_urls(args.input)
    semaphore = asyncio.Semaphore(args.concurrency)

    async def _bounded(url: str) -> dict[str, Any]:
        async with semaphore:
            return await _sniff_one(url, args)

    results = await asyncio.gather(*(_bounded(url) for url in urls))
    out = Path(args.out_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {len(results)} sniff result(s) to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
