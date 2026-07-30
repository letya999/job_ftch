from datetime import datetime
from pathlib import Path

from scripts.doc_metadata import ROOT, iter_markdown_docs, parse_front_matter


def add_front_matter(p: Path):
    content = p.read_text(encoding="utf-8")
    if content.startswith("---\n"):
        return

    title = p.stem.replace("_", " ").title()
    lines = content.split("\n")
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()

    description = f"Documentation for {p.name}"
    for line in lines[1:5]:
        line = line.strip()
        if line and not line.startswith("#"):
            description = line[:100] + ("..." if len(line) > 100 else "")
            description = description.replace('"', '\\"')
            break

    today = datetime.now().strftime("%Y-%m-%d")

    front_matter = f"""---
title: "{title}"
description: "{description}"
updated: {today}
---
"""
    p.write_text(front_matter + content, encoding="utf-8")
    print(f"Added front matter to {p.relative_to(ROOT)}")


def main():
    files = iter_markdown_docs()
    for f in files:
        data, err = parse_front_matter(f)
        if err is None and isinstance(data, dict):
            continue
        add_front_matter(f)


if __name__ == "__main__":
    main()
