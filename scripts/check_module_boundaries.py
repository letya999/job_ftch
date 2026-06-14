from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "job_ftch"
STDLIB = set(sys.stdlib_module_names)


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(ROOT).with_suffix("").parts)


def _import_targets(node: ast.AST) -> list[tuple[str, int]]:
    if isinstance(node, ast.Import):
        return [(alias.name, node.lineno) for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        if node.level > 0:
            return []
        module = node.module or ""
        return [(module, node.lineno)]
    return []


def _is_allowed_domain(target: str) -> bool:
    if not target:
        return True
    head = target.split(".")[0]
    if head in STDLIB or head == "pydantic":
        return True
    return not target.startswith(
        (
            "job_ftch.application",
            "job_ftch.infrastructure",
            "job_ftch.nodes",
            "job_ftch.sinks",
            "adapters",
        )
    )


def _is_allowed_application(target: str) -> bool:
    if not target:
        return True
    head = target.split(".")[0]
    if head in STDLIB or head in {"pydantic", "structlog", "yaml", "opentelemetry"}:
        return True
    return not target.startswith(
        ("job_ftch.infrastructure", "job_ftch.nodes", "job_ftch.sinks", "adapters")
    )


def _is_allowed_nodes(target: str) -> bool:
    if not target:
        return True
    return not (target.startswith("job_ftch.infrastructure") or target.startswith("adapters"))


def main() -> int:
    violations: list[str] = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module_name = _module_name(path)
        application_runtime_exception = module_name in {
            "job_ftch.application.builder",
            "job_ftch.application.pipeline",
            "job_ftch.application.tenant_runner",
            "job_ftch.application.source_inputs",
        }
        for node in ast.walk(tree):
            for target, lineno in _import_targets(node):
                if module_name.startswith("job_ftch.domain.") or module_name == "job_ftch.domain":
                    if not _is_allowed_domain(target):
                        violations.append(f"{path}:{lineno} disallowed domain import: {target}")
                elif (
                    module_name.startswith("job_ftch.application.")
                    or module_name == "job_ftch.application"
                ):
                    if not application_runtime_exception and not _is_allowed_application(target):
                        violations.append(
                            f"{path}:{lineno} disallowed application import: {target}"
                        )
                elif (
                    module_name.startswith("job_ftch.nodes.") or module_name == "job_ftch.nodes"
                ) and not _is_allowed_nodes(target):
                    violations.append(f"{path}:{lineno} disallowed nodes import: {target}")

    if violations:
        print("\n".join(violations))
        return 1
    print("Module boundary check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
