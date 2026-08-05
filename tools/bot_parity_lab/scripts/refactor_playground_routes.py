"""One-shot migration receipt for extracting protected-playground routes."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "paritylab" / "app.py"
TARGET = ROOT / "paritylab" / "routes" / "playground.py"

HEADER = """from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from paritylab.app import Playground, _json_response, _session_id
from paritylab.config import LabConfig
from paritylab.models import GateDecision
from paritylab.protected_site import classify_intent
from paritylab.store import ArtifactStore
from paritylab.tls import TLSConnectionRegistry

CLEARANCE_COOKIE = "parity_clearance"

"""


def _span(node: ast.AST, lines: list[str]) -> tuple[int, int]:
    start = node.lineno - 1
    end = node.end_lineno or node.lineno
    return start, end


def main() -> None:
    text = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_playground_routes"
    )
    constants = {
        node.targets[0].id: node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"_POW_PAGE", "_PUZZLE_PAGE"}
    }
    chunks = [
        ast.get_source_segment(text, constants[name]) or ""
        for name in ("_POW_PAGE", "_PUZZLE_PAGE")
    ]
    route_source = ast.get_source_segment(text, function) or ""
    chunks.append(route_source.replace("def _playground_routes(", "def playground_routes(", 1))
    TARGET.parent.mkdir(exist_ok=True)
    (TARGET.parent / "__init__.py").write_text(
        '"""HTTP route groups for the parity lab."""\n', encoding="utf-8"
    )
    TARGET.write_text(HEADER + "\n\n".join(chunks) + "\n", encoding="utf-8")

    removals = [_span(function, lines), *(_span(node, lines) for node in constants.values())]
    for start, end in sorted(removals, reverse=True):
        del lines[start:end]
    migrated = "".join(lines)
    needle = "    routes = [\n"
    replacement = "    from paritylab.routes.playground import playground_routes\n\n" + needle
    migrated = migrated.replace(needle, replacement, 1)
    migrated = migrated.replace(
        "_playground_routes(playground, config, common, store, registry)",
        "playground_routes(playground, config, common, store, registry)",
    )
    SOURCE.write_text(migrated, encoding="utf-8")


if __name__ == "__main__":
    main()
