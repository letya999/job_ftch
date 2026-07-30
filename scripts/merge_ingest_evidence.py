"""Build an auditable per-URL ingest result from independent batch checkpoints.

The script never turns a failed URL into success by inference: a merged
``parsed_ok`` exists only when a concrete input record has ``parsed_ok``.
With an explicit flag it can also replace an newer failed record with a newer,
complete terminal observation (for example a confirmed empty board).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

_AUTHORITATIVE_TERMINAL_OUTCOMES = frozenset(
    {
        "no_open_vacancies",
        "blocked_or_protected",
        "protected",
        "listing_discovery_failed",
        "detail_extraction_failed",
    }
)


def _read_records(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError(f"{path} must contain a JSON list")
    return [record for record in value if isinstance(record, dict) and record.get("url")]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--urls", type=Path, required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--url-alias",
        action="append",
        default=[],
        metavar="OLD=NEW",
        help="Map a historical evidence URL to its canonical fixture URL.",
    )
    parser.add_argument(
        "--replace-complete-terminal",
        action="store_true",
        help=(
            "Replace an newer failed record with an explicit, completed terminal "
            "observation from later evidence. Successful records are never replaced."
        ),
    )
    args = parser.parse_args()

    fixture = yaml.safe_load(args.urls.read_text(encoding="utf-8"))
    urls = fixture["urls"]
    if not isinstance(urls, list) or len(urls) != len(set(urls)):
        raise ValueError("fixture URLs must be unique")

    aliases: dict[str, str] = {}
    for raw_alias in args.url_alias:
        new, separator, new = raw_alias.partition("=")
        if not separator or not new or not new:
            raise ValueError(f"url alias must use OLD=NEW form: {raw_alias!r}")
        if new not in urls:
            raise ValueError(f"alias target must be present in fixture: {new}")
        aliases[new] = new

    merged: dict[str, dict[str, Any]] = {}
    for record in _read_records(args.base):
        original_url = record["url"]
        url = aliases.get(original_url, original_url)
        if url in merged:
            raise ValueError(f"base has multiple records for canonical URL: {url}")
        replacement = dict(record)
        replacement["url"] = url
        if url != original_url:
            replacement["canonicalized_from_url"] = original_url
        merged[url] = replacement
    extra = set(merged) - set(urls)
    if extra:
        raise ValueError(f"base contains URLs outside the fixture: {extra}")
    for url in urls:
        merged.setdefault(
            url,
            {
                "url": url,
                "parse_status": "parsed_failed",
                "failure_bucket": "not_run",
                "terminal_outcome": "not_run",
            },
        )

    for evidence_path in args.evidence:
        for original_record in _read_records(evidence_path):
            original_url = original_record["url"]
            url = aliases.get(original_url, original_url)
            record = dict(original_record)
            record["url"] = url
            if url != original_url:
                record["canonicalized_from_url"] = original_url
            if url not in merged or record.get("completion_state") == "partial":
                continue
            current = merged[url]
            current_is_complete_success = (
                current.get("parse_status") == "parsed_ok"
                and current.get("completion_state") != "partial"
            )
            if current_is_complete_success:
                continue
            is_success = record.get("parse_status") == "parsed_ok"
            is_authoritative_terminal = (
                args.replace_complete_terminal
                and record.get("completion_state") == "completed"
                and record.get("terminal_outcome") in _AUTHORITATIVE_TERMINAL_OUTCOMES
            )
            if not (is_success or is_authoritative_terminal):
                continue
            replacement = dict(record)
            replacement["merged_from_evidence"] = evidence_path.name
            replacement["prior_failure_bucket"] = current.get("failure_bucket")
            merged[url] = replacement

    result = [merged[url] for url in urls]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    parsed_ok = sum(record.get("parse_status") == "parsed_ok" for record in result)
    print(f"coverage={len(result)} parsed_ok={parsed_ok} rate={parsed_ok / len(result):.2%}")


if __name__ == "__main__":
    main()
