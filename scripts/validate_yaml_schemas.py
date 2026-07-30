#!/usr/bin/env python3
"""Validate SourceSpec and TenantConfig YAML documents (Q12).

Per the v0.0.4 MVP cleanup, individual source documents must conform to
the JSON schema and tenant documents must conform to ``TenantConfig``.
Runtime and pipeline YAML have their own typed loaders. This script is
invoked by CI and exits non-zero on a matching document validation error.

Usage:
    uv run python scripts/validate_yaml_schemas.py [path ...]

Default path is config/.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = REPO_ROOT / "config" / "sources.schema.json"
DEFAULT_TARGETS = [REPO_ROOT / "config"]


def iter_yaml_files(targets: list[Path]) -> list[Path]:
    out: list[Path] = []
    for target in targets:
        if target.is_file() and target.suffix in {".yaml", ".yml"}:
            out.append(target)
        elif target.is_dir():
            out.extend(sorted(target.rglob("*.yaml")))
            out.extend(sorted(target.rglob("*.yml")))
    return out


def validate_one(yaml_path: Path, validator: Draft202012Validator) -> list[str]:
    # The repository also keeps runtime tuning, proxy inventory, and typed
    # evidence calibration in YAML. Those have their own loaders and are not
    # SourceSpec documents; applying the source schema to them is a false
    # failure that hides real parser-manifest errors.
    if yaml_path.name in {
        "runtime.yaml",
        "runtime.dev.yaml",
        "runtime.prod.yaml",
        "proxies.yaml",
        "evidence_policy.yaml",
    }:
        return []
    # Pipeline graphs and historical baselines use their own typed graph
    # compiler.  They are not SourceSpec documents and must not be forced
    # through the source inventory schema.
    if {"pipelines", "legacy_baselines"}.intersection(yaml_path.parts):
        return []
    try:
        with yaml_path.open(encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        return [f"{yaml_path}: YAML parse error: {exc}"]
    if not isinstance(payload, dict):
        return [f"{yaml_path}: top-level YAML must be a mapping, got {type(payload).__name__}"]
    if "tenant_id" in payload or isinstance(payload.get("tenants"), list):
        from pydantic import ValidationError

        from job_ftch.domain import TenantConfig

        tenant_payloads = payload.get("tenants", [payload])
        errors: list[str] = []
        for index, tenant_payload in enumerate(tenant_payloads):
            try:
                TenantConfig.model_validate(tenant_payload)
            except ValidationError as exc:
                prefix = f"tenants/{index}" if "tenants" in payload else "<root>"
                errors.append(f"{yaml_path} -> {prefix}: {exc}")
        return errors

    # ``sources.schema.json`` describes one SourceSpec, not a runtime,
    # pipeline, or tenant aggregate. Avoid treating unrelated mappings as a
    # source merely because they are YAML.
    if "type" not in payload:
        return []

    errors: list[str] = []
    for err in validator.iter_errors(payload):
        loc = "/".join(str(x) for x in err.absolute_path) or "<root>"
        errors.append(f"{yaml_path} -> {loc}: {err.message}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "targets",
        nargs="*",
        type=Path,
        default=DEFAULT_TARGETS,
        help="Files or directories to validate (default: config/)",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA,
        help="Path to the JSON schema (default: config/sources.schema.json)",
    )
    args = parser.parse_args()

    if not args.schema.exists():
        print(f"schema not found: {args.schema}", file=sys.stderr)
        return 2

    import json

    with args.schema.open(encoding="utf-8") as handle:
        schema = json.load(handle)
    validator = Draft202012Validator(schema)

    yaml_files = [p for p in iter_yaml_files(args.targets) if p.exists()]
    if not yaml_files:
        print(f"no YAML files found in: {args.targets}", file=sys.stderr)
        return 0

    all_errors: list[str] = []
    for path in yaml_files:
        all_errors.extend(validate_one(path, validator))

    if all_errors:
        print(f"validation FAILED for {len(all_errors)} error(s):", file=sys.stderr)
        for err in all_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(f"validation OK: {len(yaml_files)} YAML file(s) match {args.schema.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
