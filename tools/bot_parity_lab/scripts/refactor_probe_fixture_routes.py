"""One-shot receipt for deleting handlers moved to route-builder modules."""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "paritylab" / "app.py"
TOP_LEVEL = {"_shannon_entropy", "_printable_ratio", "_likely_base64", "_json_key_shape"}
NESTED = {"probe", "events", "beacon", "opaque", "protection_fixture", "protection_contract"}


def main() -> None:
    text = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(text)
    create_app = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "create_app"
    )
    selected: list[ast.FunctionDef | ast.AsyncFunctionDef] = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in TOP_LEVEL
    ]
    selected.extend(
        node
        for node in create_app.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in NESTED
    )
    found = {node.name for node in selected}
    expected = TOP_LEVEL | NESTED
    if found != expected:
        raise SystemExit(f"expected moved handlers {sorted(expected)}, found {sorted(found)}")
    lines = text.splitlines(keepends=True)
    for node in sorted(selected, key=lambda item: item.lineno, reverse=True):
        del lines[node.lineno - 1 : node.end_lineno]
    SOURCE.write_text("".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
