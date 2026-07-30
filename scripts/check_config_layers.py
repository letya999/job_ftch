"""Check configuration layers for deprecated patterns.

Exits non-zero when a deprecated key (openai_model, relevance_llm_model,
llm_backend) appears in a tenant YAML file. These belong in the runtime
YAML or environment layer, not in per-tenant config.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
TENANT_DIR = ROOT / "job_ftch" / "adapters" / "telegram_bot" / "config" / "tenants"

# Keys that must not appear in tenant YAML - they belong to the runtime layer
DEPRECATED_TENANT_KEYS = {"openai_model", "relevance_llm_model", "llm_backend"}


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file and return its top-level mapping."""
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _check_deprecated_tenant_keys(tenant_files: list[Path]) -> list[str]:
    """Find deprecated keys in tenant YAML files."""
    violations: list[str] = []
    for path in tenant_files:
        data = _load_yaml(path)
        for key in sorted(DEPRECATED_TENANT_KEYS & set(data.keys())):
            violations.append(
                f"{path.name}: '{key}' should not be in tenant config "
                f"(move to runtime YAML or environment)"
            )
    return violations


def main() -> int:
    tenant_files = sorted(TENANT_DIR.glob("*.yaml")) if TENANT_DIR.exists() else []
    violations = _check_deprecated_tenant_keys(tenant_files)

    if violations:
        print("Config layer violations:")
        for v in violations:
            print(f"  {v}")
        return 1

    print("Config layer check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
