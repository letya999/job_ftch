import argparse
from datetime import datetime, timedelta

from scripts.doc_metadata import ROOT, iter_markdown_docs, normalize_updated, parse_front_matter


def main():
    parser = argparse.ArgumentParser(description="Find stale documentation.")
    parser.add_argument(
        "--days", type=int, default=180, help="Number of days before a document is considered stale"
    )
    args = parser.parse_args()

    files = iter_markdown_docs()
    stale_files: list[tuple[object, datetime]] = []
    now = datetime.now()
    threshold = timedelta(days=args.days)

    for f in files:
        data, err = parse_front_matter(f)
        if err or not isinstance(data, dict):
            continue

        upd = data.get("updated")
        if not upd:
            continue

        normalized, error = normalize_updated(upd)
        if error is not None or normalized is None:
            continue
        upd_date = datetime.strptime(normalized, "%Y-%m-%d")
        if now - upd_date > threshold:
            stale_files.append((f, upd_date))

    if stale_files:
        print(f"Found {len(stale_files)} stale documents (newer than {args.days} days):")
        for f, d in sorted(stale_files, key=lambda x: x[1]):
            print(f" - {f.relative_to(ROOT)} (Last updated: {d.strftime('%Y-%m-%d')})")
    else:
        print(f"No stale documents found (threshold: {args.days} days).")


if __name__ == "__main__":
    main()
