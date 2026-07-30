"""Trace the resolved value and origin layer for every configuration key.

Layers (highest to lowest priority):
  1. Environment variables (JOB_FTCH_*)
  2. .env / .env.dev / .env.prod
  3. Tenant YAML (job_ftch/adapters/telegram_bot/config/tenants/*.yaml)
  4. Runtime YAML (config/runtime.dev.yaml or config/runtime.prod.yaml)
  5. Base runtime YAML (config/runtime.yaml)
  6. Settings class defaults (job_ftch/config.py)

The output is a dict mapping each key to its resolved value and the layer
it came from. Used for startup logging and provenance recording.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_LAYER_NAMES = (
    "env",
    "dotenv",
    "tenant_yaml",
    "runtime_yaml",
    "base_runtime_yaml",
    "default",
)


def _load_yaml_flat(path: Path) -> dict[str, Any]:
    """Load a YAML file and return its top-level keys."""
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def resolve_config_layers(
    *,
    tenant_path: Path | None = None,
    runtime_paths: list[Path] | None = None,
    settings: Any = None,
) -> dict[str, dict[str, Any]]:
    """For each configuration key, identify the value and its source layer.

    Args:
        tenant_path: Path to the tenant YAML file.
        runtime_paths: Ordered list of runtime YAML files (base first).
        settings: Resolved Settings object (for defaults and final values).

    Returns:
        Mapping of key -> {"value": ..., "layer": ..., "layers_present": [...]}.
    """
    # Collect values from each layer
    tenant_values = _load_yaml_flat(tenant_path) if tenant_path else {}

    runtime_values: dict[str, Any] = {}
    base_runtime_values: dict[str, Any] = {}
    if runtime_paths:
        for idx, path in enumerate(runtime_paths):
            values = _load_yaml_flat(path)
            if idx == 0:
                base_runtime_values = values
            else:
                runtime_values.update(values)

    env_prefix = "JOB_FTCH_"
    env_values: dict[str, str] = {}
    for key, value in os.environ.items():
        if key.startswith(env_prefix):
            config_key = key[len(env_prefix) :].lower()
            env_values[config_key] = value

    # Determine all known keys from the settings defaults
    all_keys: set[str] = set()
    defaults: dict[str, Any] = {}
    if settings is not None:
        try:
            defaults = settings.model_dump(mode="python")
            all_keys.update(defaults.keys())
        except Exception:
            pass

    all_keys.update(tenant_values.keys())
    all_keys.update(runtime_values.keys())
    all_keys.update(base_runtime_values.keys())
    all_keys.update(env_values.keys())

    result: dict[str, dict[str, Any]] = {}
    for key in sorted(all_keys):
        layers_present = []
        resolved_value = defaults.get(key)
        resolved_layer = "default"

        if key in defaults:
            layers_present.append("default")

        if key in base_runtime_values:
            layers_present.append("base_runtime_yaml")
            resolved_value = base_runtime_values[key]
            resolved_layer = "base_runtime_yaml"

        if key in runtime_values:
            layers_present.append("runtime_yaml")
            resolved_value = runtime_values[key]
            resolved_layer = "runtime_yaml"

        if key in tenant_values:
            layers_present.append("tenant_yaml")
            resolved_value = tenant_values[key]
            resolved_layer = "tenant_yaml"

        if key in env_values:
            layers_present.append("env")
            resolved_value = env_values[key]
            resolved_layer = "env"

        # Use the final settings value when available (it is the actual
        # resolved value after pydantic processing)
        final_value = defaults.get(key, resolved_value)

        result[key] = {
            "value": final_value,
            "layer": resolved_layer,
            "layers_present": layers_present,
        }

    return result


def log_resolution(resolution: dict[str, dict[str, Any]]) -> None:
    """Log the resolved configuration at startup."""
    for key, info in resolution.items():
        if len(info["layers_present"]) > 1:
            logger.info(
                "config %s = %r (from %s, also in: %s)",
                key,
                info["value"],
                info["layer"],
                ", ".join(layer for layer in info["layers_present"] if layer != info["layer"]),
            )


def resolution_for_provenance(
    resolution: dict[str, dict[str, Any]],
) -> dict[str, dict[str, str]]:
    """Return a provenance-safe subset (no secrets) of the resolution."""
    secret_patterns = {"dsn", "key", "secret", "password", "token"}
    result: dict[str, dict[str, str]] = {}
    for key, info in resolution.items():
        is_secret = any(pattern in key.lower() for pattern in secret_patterns)
        result[key] = {
            "layer": str(info["layer"]),
            "value": "***" if is_secret else str(info["value"]),
        }
    return result
