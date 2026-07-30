"""Validate the real-world career-site URL fixture before a batch ingest."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlsplit

import yaml


def validate_fixture(path: Path) -> list[str]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return ["fixture root must be a mapping"]
    urls = payload.get("urls")
    if not isinstance(urls, list):
        return ["urls must be a list"]

    errors: list[str] = []
    seen: set[str] = set()
    for index, url in enumerate(urls):
        if not isinstance(url, str):
            errors.append(f"urls[{index}] must be a string")
            continue
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append(f"urls[{index}] is not an absolute HTTP(S) URL: {url!r}")
        if " - " in url:
            errors.append(f"urls[{index}] looks like concatenated URLs: {url!r}")
        if url in seen:
            errors.append(f"urls[{index}] duplicates {url!r}")
        seen.add(url)

    expected_count = payload.get("expected_url_count")
    if expected_count is not None and expected_count != len(urls):
        errors.append(f"expected_url_count={expected_count}, actual={len(urls)}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path", type=Path, nargs="?", default=Path("fixtures/sources/career_sites_cis_303.yaml")
    )
    args = parser.parse_args()
    errors = validate_fixture(args.path)
    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"{args.path}: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
