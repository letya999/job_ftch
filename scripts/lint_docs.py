import subprocess
import sys

from scripts.doc_metadata import (
    REQUIRED_FIELDS,
    ROOT,
    iter_markdown_docs,
    normalize_updated,
    parse_front_matter,
)


def main():
    files = iter_markdown_docs()
    errors: list[str] = []

    for f in files:
        data, err = parse_front_matter(f)
        if err:
            errors.append(f"{f.relative_to(ROOT)}: {err}")
            continue

        assert isinstance(data, dict)
        for req in REQUIRED_FIELDS:
            if req not in data:
                errors.append(f"{f.relative_to(ROOT)}: Missing required field '{req}'")
        title = str(data.get("title", "")).strip()
        description = str(data.get("description", "")).strip()
        if not title:
            errors.append(f"{f.relative_to(ROOT)}: 'title' must be a non-empty string")
        if not description:
            errors.append(f"{f.relative_to(ROOT)}: 'description' must be a non-empty string")
        _, updated_error = normalize_updated(data.get("updated"))
        if updated_error is not None:
            errors.append(f"{f.relative_to(ROOT)}: {updated_error}")

    try:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build_index_docs.py"), "--check"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        details = (e.stdout or "") + (e.stderr or "")
        errors.append(f"Index generation check failed:\n{details.strip()}")

    if errors:
        print("Linting failed with errors:")
        for e in errors:
            print(f" - {e}")
        sys.exit(1)

    print("Linting passed successfully.")
    sys.exit(0)


if __name__ == "__main__":
    main()
