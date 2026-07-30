"""Collect reproducible HTTP evidence for career-site ``empty_result`` probes."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

import httpx
from selectolax.lexbor import LexborHTMLParser

_JOB_TERMS = re.compile(
    r"job|career|vacan|position|opening|role|hiring|работ|ваканс|karier|stanowisk|emploi",
    re.IGNORECASE,
)


def _analyse_html(html: str, base_url: str) -> dict[str, Any]:
    parser = LexborHTMLParser(html)
    title_node = parser.css_first("title")
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in parser.css("a[href]"):
        href = anchor.attributes.get("href", "").strip()
        text = " ".join(anchor.text(separator=" ", strip=True).split())
        haystack = f"{href} {text}"
        if not href or not _JOB_TERMS.search(haystack) or href in seen:
            continue
        seen.add(href)
        candidates.append({"href": href, "text": text[:160]})
        if len(candidates) == 12:
            break
    return {
        "title": title_node.text(strip=True)[:300] if title_node else "",
        "job_term_count": len(_JOB_TERMS.findall(html)),
        "jobposting_jsonld_count": html.casefold().count("jobposting"),
        "has_next_data": "__NEXT_DATA__" in html,
        "candidate_links": candidates,
        "base_url": base_url,
    }


async def _audit_one(client: httpx.AsyncClient, row: dict[str, Any]) -> dict[str, Any]:
    url = row["url"]
    try:
        response = await client.get(url)
        content_type = response.headers.get("content-type", "")
        result: dict[str, Any] = {
            "url": url,
            "http_status": response.status_code,
            "final_url": str(response.url),
            "content_type": content_type,
            "body_bytes": len(response.content),
        }
        if "html" in content_type.casefold():
            result.update(_analyse_html(response.text, str(response.url)))
        return result
    except Exception as exc:
        return {"url": url, "error": f"{type(exc).__name__}: {exc}"}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="artifacts/debug/ingest_all_dynamic_final.json")
    parser.add_argument("--out", default="artifacts/debug/empty_result_http_audit.json")
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()

    rows = json.loads(Path(args.input).read_text(encoding="utf-8"))
    # The batch probe deliberately calls this outcome ``unconfirmed_empty``:
    # no vacancy was emitted, but that is not proof the board is empty.  Keep
    # the legacy name as well so historical artifacts remain auditable.
    empty_rows = [
        row for row in rows if row.get("failure_bucket") in {"empty_result", "unconfirmed_empty"}
    ]
    semaphore = asyncio.Semaphore(args.concurrency)

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=30.0,
        headers={"User-Agent": "Mozilla/5.0"},
    ) as client:

        async def bounded(row: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                return await _audit_one(client, row)

        results = await asyncio.gather(*(bounded(row) for row in empty_rows))

    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Audited {len(results)} unconfirmed-empty URLs -> {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
