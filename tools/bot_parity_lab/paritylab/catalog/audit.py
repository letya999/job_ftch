from __future__ import annotations

import ast
import fnmatch
from pathlib import Path

from paritylab.catalog.registry import Catalog


def discover_static_finding_codes(source: Path) -> set[str]:
    """Return literal finding codes emitted by calls to the scorer helper."""
    codes: set[str] = set()
    sources = sorted(source.glob("*.py")) if source.is_dir() else [source]
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "_finding" or len(node.args) < 2:
                continue
            code = node.args[1]
            if isinstance(code, ast.Constant) and isinstance(code.value, str):
                codes.add(code.value)
    return codes


def undocumented_codes(catalog: Catalog, codes: set[str]) -> tuple[str, ...]:
    documented = {item.code for item in catalog.findings}
    return tuple(
        sorted(
            code
            for code in codes
            if not any(fnmatch.fnmatchcase(code, pattern) for pattern in documented)
        )
    )
